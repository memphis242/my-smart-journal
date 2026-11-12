"""A worker SIGKILLed mid-batch is recovered by the next worker exactly once.

`cleanup` is routed to an OpenAI-compatible chat-completions server running in this test
process. It records each request and holds every response until the test releases it, so
the worker is reliably inside a running job when it is killed.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

import pytest

from .msj_harness import STARTUP_TIMEOUT_S, MsjFactory, MsjInstance

pytestmark = pytest.mark.integration

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


@pytest.fixture
def chat_server() -> Iterator[HeldChatServer]:
    server = HeldChatServer()
    try:
        yield server
    finally:
        server.close()


def _cleanup_job(msj: MsjInstance, entry_id: str) -> dict[str, Any]:
    jobs = [job for job in msj.jobs() if job["entry_id"] == entry_id and job["kind"] == "cleanup"]
    assert len(jobs) == 1, jobs
    return jobs[0]


def test_sigkilled_worker_job_is_reclaimed_and_completes_once(
    msj_factory: MsjFactory, chat_server: HeldChatServer
) -> None:
    msj = msj_factory(
        {
            "provider_timeout_s": 60,
            "lease_s": 120,
            "tasks": {
                "cleanup": {
                    "provider": "openai",
                    "model": "fake-openai",
                    "base_url": chat_server.base_url,
                }
            },
        }
    ).boot()
    first_worker = msj.processes[-1]
    entry = msj.create_text_entry("rebuilt atlas index after the outage")
    msj.force_batch()

    if not chat_server.wait_for_requests(1, STARTUP_TIMEOUT_S):
        pytest.fail(f"worker never called the chat server\n{msj.diagnostics()}")
    request = chat_server.requests()[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["model"] == "fake-openai"
    running = _cleanup_job(msj, entry["id"])
    assert running["status"] == "running", running
    assert running["attempts"] == 1, running
    assert msj.queue()["batch"]["running"] is True

    first_worker.kill_hard()
    _ = msj.start_worker()
    # The new worker's start-up batch normally holds the lease already (409).
    _ = msj.request_batch()

    if not chat_server.wait_for_requests(2, STARTUP_TIMEOUT_S):
        pytest.fail(f"replacement worker never retried cleanup\n{msj.diagnostics()}")
    reclaimed = _cleanup_job(msj, entry["id"])
    assert reclaimed["status"] == "running", reclaimed
    assert reclaimed["attempts"] == 2, reclaimed
    assert reclaimed["last_error"], "a reclaimed job keeps a reclaim reason in last_error"
    lease = msj.queue()["batch"]
    assert lease["running"] is True
    assert lease["last_error"] is not None, lease
    assert lease["last_error"]["code"] == "BATCH_LEASE_LOST", lease

    chat_server.release()
    msj.process_entries([entry["id"]])

    history = msj.entry(entry["id"], history=True)
    cleaned = [a for a in history["artifacts"] if a["kind"] == "cleaned"]
    assert len(cleaned) == 1, f"duplicate cleanup output: {cleaned}"
    assert cleaned[0]["revision"] == 1
    assert cleaned[0]["content"] == CLEANED
    assert cleaned[0]["model"] == "fake-openai"
    for kind in ("summary", "extraction"):
        assert sum(1 for a in history["artifacts"] if a["kind"] == kind) == 1, kind

    done = _cleanup_job(msj, entry["id"])
    assert done["status"] == "done", done
    assert done["attempts"] == 2, done
    assert done["last_error"] is None, done
    assert all(job["status"] == "done" for job in history["jobs"]), history["jobs"]
    assert len(chat_server.requests()) == 2, "cleanup must not be called a third time"

    queue = msj.queue()
    assert queue["counts"]["running"] == 0, queue
    assert queue["counts"]["failed"] == 0, queue
    assert not [item for item in queue["items"] if item["status"] in {"running", "failed"}]
    assert queue["batch"]["running"] is False
