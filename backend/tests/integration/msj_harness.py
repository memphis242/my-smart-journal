"""Process-level harness: real `msj` subprocesses driven over loopback HTTP.

Each `MsjInstance` owns one temporary data directory, one generated TOML config, and the
`msj serve`/`msj worker` processes it starts. Subprocess output goes to log files in the
instance directory (never to pipes that can fill), and every failure message carries the
log tails so a failed run is diagnosable from the pytest report alone.
"""

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final, Protocol

import httpx
import pytest

# Ports owned by other long-running instances on this machine; never use them.
RESERVED_PORTS: Final = frozenset({18765, 18770, *range(18780, 18790)})
STARTUP_TIMEOUT_S: Final = 30.0
STOP_TIMEOUT_S: Final = 10.0
CLI_TIMEOUT_S: Final = 60.0
PROCESSING_TIMEOUT_S: Final = 90.0
LOG_TAIL_CHARS: Final = 3000
TASK_KINDS: Final = ("transcribe", "cleanup", "review", "summarize", "extract", "compile")
# Credentials a developer shell may carry; a test process must never pass them on.
_SCRUBBED_ENV: Final = frozenset({"RUNPOD_API_KEY", "OPENAI_API_KEY"})
_BIND_FAILURE = re.compile(r"address already in use|errno 98", re.IGNORECASE)


def default_config() -> dict[str, Any]:
    """All-fake routing, fast worker ticks, and a schedule long enough that only forced
    batches (plus the worker's start-up pass) run during a test."""
    return {
        "batch_interval_s": 86400,
        "worker_tick_s": 0.2,
        "insecure_cookies": True,
        "bearer_rate_limit": 1000,
        "login_rate_limit": 1000,
        "max_attempts": 3,
        "retry_base_s": 0.5,
        "provider_timeout_s": 60,
        "lease_s": 120,
        "embedding": {"provider": "fake"},
        "tasks": {kind: {"provider": "fake", "model": "fake"} for kind in TASK_KINDS},
    }


def merge_config(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        previous = merged.get(key)
        if isinstance(previous, Mapping) and isinstance(value, Mapping):
            merged[key] = merge_config(previous, value)
        else:
            merged[key] = value
    return merged


def _toml_value(value: object) -> str:
    match value:
        case bool():
            return "true" if value else "false"
        case int() | float():
            return repr(value)
        case str():
            # JSON string escapes are a subset of TOML basic-string escapes.
            return json.dumps(value)
        case list():
            return "[" + ", ".join(_toml_value(item) for item in value) + "]"
        case _:
            raise TypeError(f"Unsupported TOML value type {type(value).__name__}")


def to_toml(document: Mapping[str, Any], prefix: str = "") -> str:
    lines = [
        f"{key} = {_toml_value(value)}"
        for key, value in document.items()
        if not isinstance(value, Mapping)
    ]
    for key, value in document.items():
        if isinstance(value, Mapping):
            name = f"{prefix}{key}"
            lines.extend((f"[{name}]", to_toml(value, f"{name}.")))
    return "\n".join(line for line in lines if line) + "\n"


def tail(path: Path, limit: int = LOG_TAIL_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "<no output file>"
    return text if len(text) <= limit else "…" + text[-limit:]


def free_port() -> int:
    for _ in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port not in RESERVED_PORTS:
            return port
    raise AssertionError("Could not find a free loopback port outside the reserved set")


def wait_until[T](
    condition: Callable[[], T | None],
    *,
    timeout: float,
    describe: Callable[[], str],
    interval: float = 0.05,
) -> T:
    """Poll `condition` until it returns a non-None value or fail with `describe()`."""
    assert timeout > 0
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value is not None:
            return value
        if time.monotonic() >= deadline:
            pytest.fail(f"Timed out after {timeout:g}s waiting: {describe()}")
        time.sleep(interval)


class ManagedProcess:
    """One `msj` subprocess with stdout/stderr redirected to files."""

    def __init__(
        self, name: str, args: list[str], *, cwd: Path, env: Mapping[str, str], log_dir: Path
    ) -> None:
        self.name: Final = name
        self.stdout_path: Final = log_dir / f"{name}.stdout.log"
        self.stderr_path: Final = log_dir / f"{name}.stderr.log"
        # The child keeps its own duplicated descriptors; ours close right after spawning.
        with self.stdout_path.open("wb") as stdout, self.stderr_path.open("wb") as stderr:
            self._popen: Final = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, cwd=cwd, env=env
            )

    @property
    def pid(self) -> int:
        return self._popen.pid

    def exit_code(self) -> int | None:
        return self._popen.poll()

    def stop(self) -> None:
        """SIGTERM, then SIGKILL after a bounded wait; always reaps the child."""
        if self._popen.poll() is None:
            self._popen.terminate()
            try:
                _ = self._popen.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self._popen.kill()
                _ = self._popen.wait(timeout=STOP_TIMEOUT_S)

    def terminate_and_wait(self, timeout: float) -> int:
        """SIGTERM and return the exit code; SIGKILL and fail if it outlives `timeout`."""
        assert self._popen.poll() is None, f"{self.name} already exited"
        self._popen.terminate()
        try:
            return self._popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._popen.kill()
            _ = self._popen.wait(timeout=STOP_TIMEOUT_S)
            pytest.fail(f"{self.name} ignored SIGTERM for {timeout:g} s\n{self.logs()}")

    def kill_hard(self) -> None:
        """SIGKILL without any chance to clean up, then reap so the PID really is gone."""
        assert self._popen.poll() is None, f"{self.name} already exited"
        self._popen.send_signal(signal.SIGKILL)
        returncode = self._popen.wait(timeout=STOP_TIMEOUT_S)
        assert returncode == -signal.SIGKILL, f"{self.name} exited with {returncode}"

    def logs(self) -> str:
        code = self._popen.poll()
        state = "running" if code is None else f"exited {code}"
        return (
            f"--- {self.name} (pid {self._popen.pid}, {state}) stderr ---\n"
            f"{tail(self.stderr_path)}\n"
            f"--- {self.name} stdout ---\n{tail(self.stdout_path)}"
        )

    def output(self) -> str:
        return self.stdout_path.read_text(encoding="utf-8", errors="replace") + (
            self.stderr_path.read_text(encoding="utf-8", errors="replace")
        )


class MsjInstance:
    """A data directory, config, and the serve/worker processes started against them."""

    def __init__(self, root: Path, config: Mapping[str, Any]) -> None:
        self.root: Final = root
        self.data_dir: Final = root / "data"
        self.log_dir: Final = root / "logs"
        self.config_path: Final = root / "config.toml"
        self.log_dir.mkdir(parents=True, exist_ok=False)
        self.data_dir.mkdir(mode=0o700, exist_ok=False)
        text = to_toml(config)
        assert tomllib.loads(text) == json.loads(json.dumps(config)), "TOML round trip"
        _ = self.config_path.write_text(text, encoding="utf-8")
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("MSJ_") and key not in _SCRUBBED_ENV
        }
        environment |= {
            "MSJ_CONFIG_FILE": str(self.config_path),
            "MSJ_DATA_DIR": str(self.data_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "HF_HUB_OFFLINE": "1",
        }
        self.env: Final[Mapping[str, str]] = environment
        self.processes: Final[list[ManagedProcess]] = []
        self._clients: Final[list[httpx.Client]] = []
        self._cli_log: Final[list[str]] = []
        self._worker_count = 0
        self.server: ManagedProcess | None = None
        self.base_url: str | None = None
        self.token: str | None = None
        self._api: httpx.Client | None = None

    # ----- lifecycle -------------------------------------------------------------------

    def run_cli(self, *args: str, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, "-m", "msj", *args]
        result = subprocess.run(
            command,
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_S,
            check=False,
        )
        self._cli_log.append(
            f"$ msj {' '.join(args)} -> {result.returncode}\n"
            f"stdout: {result.stdout[-LOG_TAIL_CHARS:]}\nstderr: {result.stderr[-LOG_TAIL_CHARS:]}"
        )
        if expect_success and result.returncode != 0:
            pytest.fail(f"`msj {' '.join(args)}` failed\n{self.diagnostics()}")
        return result

    def migrate(self) -> None:
        _ = self.run_cli("migrate")

    def create_token(self) -> str:
        lines = [line for line in self.run_cli("create-token").stdout.splitlines() if line]
        assert lines, f"create-token printed no token\n{self.diagnostics()}"
        token = lines[-1].strip()
        assert token, "create-token printed an empty token"
        self.token = token
        return token

    def start_server(self) -> str:
        assert self.server is None, "server already started"
        for attempt in range(5):
            port = free_port()
            process = ManagedProcess(
                f"serve-{attempt}",
                [sys.executable, "-m", "msj", "serve", "--host", "127.0.0.1", "--port", str(port)],
                cwd=self.root,
                env=self.env,
                log_dir=self.log_dir,
            )
            self.processes.append(process)
            base_url = f"http://127.0.0.1:{port}"
            if self._await_health(process, base_url):
                self.server = process
                self.base_url = base_url
                return base_url
            process.stop()
            if not _BIND_FAILURE.search(process.output()):
                pytest.fail(f"msj serve did not become ready\n{self.diagnostics()}")
        pytest.fail(f"msj serve could not bind a free port in 5 attempts\n{self.diagnostics()}")

    def _await_health(self, process: ManagedProcess, base_url: str) -> bool:
        """True once *this* process serves /health; False if it exited (e.g. bind failure).

        A healthy answer alone could come from another server that took the port between
        probing and binding, so uvicorn's own bind confirmation must be in our log too.
        """
        bound = f"Uvicorn running on {base_url}"
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        with httpx.Client(base_url=base_url, timeout=2.0, trust_env=False) as probe:
            while time.monotonic() < deadline:
                if process.exit_code() is not None:
                    return False
                if bound in process.output():
                    with contextlib.suppress(httpx.TransportError):
                        if probe.get("/api/v1/health").status_code == 200:
                            return True
                time.sleep(0.05)
        pytest.fail(
            f"msj serve did not answer /api/v1/health within {STARTUP_TIMEOUT_S:g}s\n"
            f"{self.diagnostics()}"
        )

    def start_worker(self) -> ManagedProcess:
        self._worker_count += 1
        worker = ManagedProcess(
            f"worker-{self._worker_count}",
            [sys.executable, "-m", "msj", "worker"],
            cwd=self.root,
            env=self.env,
            log_dir=self.log_dir,
        )
        self.processes.append(worker)
        return worker

    def boot(self, *, worker: bool = True) -> MsjInstance:
        """Migrate, issue a token, start the server and (optionally) a worker that has
        finished its start-up batch."""
        self.migrate()
        _ = self.create_token()
        _ = self.start_server()
        if worker:
            _ = self.start_worker()
            self.wait_worker_idle()
        return self

    def close(self) -> None:
        for client in self._clients:
            client.close()
        for process in reversed(self.processes):
            process.stop()

    # ----- HTTP ------------------------------------------------------------------------

    def client(
        self,
        *,
        bearer: str | None = None,
        cookies: Mapping[str, str] | None = None,
        authenticated: bool = True,
    ) -> httpx.Client:
        assert self.base_url is not None, "start the server first"
        headers: dict[str, str] = {}
        token = bearer if bearer is not None else self.token if authenticated else None
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            cookies=dict(cookies) if cookies is not None else None,
            timeout=30.0,
            trust_env=False,
        )
        self._clients.append(client)
        return client

    @property
    def api(self) -> httpx.Client:
        if self._api is None:
            assert self.token is not None, "create a token first"
            self._api = self.client()
        return self._api

    def expect(self, response: httpx.Response, status: int) -> Any:
        if response.status_code != status:
            pytest.fail(
                f"{response.request.method} {response.request.url.path} returned "
                f"{response.status_code}, expected {status}: {response.text[:2000]}\n"
                f"{self.diagnostics()}"
            )
        if not response.content:
            return None
        media_type = response.headers.get("content-type", "").split(";")[0].strip()
        if media_type in {"application/json", "application/problem+json"}:
            return response.json()
        return response.text

    def expect_problem(self, response: httpx.Response, status: int, code: str) -> dict[str, Any]:
        body = self.expect(response, status)
        assert isinstance(body, dict), f"expected problem JSON, got {body!r}"
        assert body.get("code") == code, f"expected code {code}, got {body}"
        return body

    def create_text_entry(self, text: str, **fields: Any) -> dict[str, Any]:
        entry = self.expect(self.api.post("/api/v1/entries", json={"text": text, **fields}), 201)
        assert isinstance(entry, dict)
        return entry

    def entry(self, entry_id: str, *, history: bool = False) -> dict[str, Any]:
        params = {"history": "1"} if history else None
        entry = self.expect(self.api.get(f"/api/v1/entries/{entry_id}", params=params), 200)
        assert isinstance(entry, dict)
        return entry

    def queue(self) -> dict[str, Any]:
        queue = self.expect(self.api.get("/api/v1/queue"), 200)
        assert isinstance(queue, dict)
        return queue

    def jobs(self) -> list[dict[str, Any]]:
        jobs = self.expect(self.api.get("/api/v1/jobs"), 200)
        assert isinstance(jobs, list)
        return jobs

    def request_batch(self) -> int:
        """Ask the worker for a batch; 409 means one is already running."""
        response = self.api.post("/api/v1/batches/run")
        if response.status_code not in {202, 409}:
            self.expect(response, 202)
        if response.status_code == 409:
            _ = self.expect_problem(response, 409, "BATCH_ALREADY_RUNNING")
        return response.status_code

    def force_batch(self) -> None:
        """Record a batch request, waiting out any batch that currently holds the lease."""
        _ = wait_until(
            lambda: True if self.request_batch() == 202 else None,
            timeout=PROCESSING_TIMEOUT_S,
            describe=lambda: f"a batch request to be accepted\n{self.diagnostics()}",
            interval=0.1,
        )

    def process_until(
        self,
        condition: Callable[[], bool],
        *,
        what: str,
        timeout: float = PROCESSING_TIMEOUT_S,
    ) -> None:
        """Keep requesting batches while the worker is idle until `condition` holds."""
        last_queue: dict[str, Any] = {}

        def settled() -> bool | None:
            nonlocal last_queue
            if condition():
                return True
            last_queue = self.queue()
            batch = last_queue["batch"]
            if not batch["running"] and not batch["request_pending"]:
                _ = self.request_batch()
            return None

        _ = wait_until(
            settled,
            timeout=timeout,
            describe=lambda: (
                f"{what}\nlast queue: {json.dumps(last_queue)[:3000]}\n{self.diagnostics()}"
            ),
            interval=0.1,
        )

    def entry_status(self, entry_id: str) -> str:
        return str(self.entry(entry_id)["status"])

    def process_entries(
        self, entry_ids: list[str], *, timeout: float = PROCESSING_TIMEOUT_S
    ) -> None:
        self.process_until(
            lambda: all(self.entry_status(entry_id) == "done" for entry_id in entry_ids),
            what=f"entries {entry_ids} to reach status done",
            timeout=timeout,
        )

    def wait_worker_idle(self) -> None:
        """Wait for a fresh worker heartbeat and the end of the worker's start-up batch."""

        def idle() -> bool | None:
            state = self.queue()
            ready = (
                not state["worker"]["stale"]
                and not state["batch"]["running"]
                and state["batch"]["last_finished_at"] is not None
            )
            return True if ready else None

        _ = wait_until(
            idle,
            timeout=STARTUP_TIMEOUT_S,
            describe=lambda: f"the worker to finish its start-up batch\n{self.diagnostics()}",
            interval=0.1,
        )

    # ----- diagnostics -----------------------------------------------------------------

    def diagnostics(self) -> str:
        parts = [f"instance directory: {self.root}", *self._cli_log[-6:]]
        parts.extend(process.logs() for process in self.processes)
        return "\n".join(parts)

    def all_process_output(self) -> str:
        return "\n".join(process.output() for process in self.processes)


class MsjFactory(Protocol):
    def __call__(self, config: Mapping[str, Any] | None = None) -> MsjInstance: ...


@contextlib.contextmanager
def msj_instances(root: Path) -> Iterator[MsjFactory]:
    """Yield a factory of instances under `root`; stop every started process on exit."""
    instances: list[MsjInstance] = []

    def make(config: Mapping[str, Any] | None = None) -> MsjInstance:
        instance = MsjInstance(
            root / f"msj-{len(instances)}", merge_config(default_config(), config or {})
        )
        instances.append(instance)
        return instance

    try:
        yield make
    finally:
        for instance in reversed(instances):
            instance.close()


# Loopback OpenAI-compatible chat server that holds responses, for mid-job process tests.
CLEANED: Final = "Cleaned by the loopback chat server: rebuilt the Atlas index."
HOLD_TIMEOUT_S: Final = 120.0


class _JoiningServer(ThreadingHTTPServer):
    # Non-daemon handler threads are joined by server_close(), so no socket outlives a test.
    daemon_threads = False


class HeldChatServer:
    """Loopback `/v1/chat/completions` that answers only after `release()`."""

    def __init__(self) -> None:
        self._released = threading.Event()
        self._arrived = threading.Condition()
        self._requests: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                with owner._arrived:
                    owner._requests.append({"path": self.path, "body": body})
                    owner._arrived.notify_all()
                released = owner._released.wait(HOLD_TIMEOUT_S)
                payload = json.dumps(
                    {
                        "model": "fake-openai",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": CLEANED},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                ).encode()
                try:
                    self.send_response(200 if released else 503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    _ = self.wfile.write(payload)
                except BrokenPipeError, ConnectionResetError:
                    pass  # the killed worker's connection is gone; nothing to answer

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

        self._server: Final = _JoiningServer(("127.0.0.1", 0), Handler)
        self._thread: Final = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        assert host == "127.0.0.1"
        return f"http://127.0.0.1:{port}/v1"

    def requests(self) -> list[dict[str, Any]]:
        with self._arrived:
            return list(self._requests)

    def wait_for_requests(self, count: int, timeout: float) -> bool:
        with self._arrived:
            return self._arrived.wait_for(lambda: len(self._requests) >= count, timeout)

    def release(self) -> None:
        self._released.set()

    def close(self) -> None:
        self._released.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)
        assert not self._thread.is_alive()
