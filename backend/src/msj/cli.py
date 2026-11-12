"""Command line entry points for the journal service."""

import argparse
import logging
import os
import signal
import threading
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

import uvicorn

from .api import create_app
from .auth import TokenReissueRequired, TokenSecretError, check_token_secret, create_token
from .batch import BatchCoordinator
from .config import Settings, load_settings
from .diagnostics import safe_exception
from .embedding import EmbeddingService
from .pipeline import Runner
from .storage import Conflict, Store

_logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="msj", description="Private single-user journal service")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("worker", help="process queued jobs on the configured schedule")
    commands.add_parser("run-batch", help="process pending jobs once, including dependency waves")
    commands.add_parser("migrate", help="apply database migrations")
    commands.add_parser("create-token", help="create an API bearer token")
    commands.add_parser("embedding-download", help="download the configured embedding model")
    return parser


def _store(settings: Settings) -> Store:
    store = Store(settings)
    store.migrate()
    try:
        check_token_secret(store)
    except (TokenReissueRequired, TokenSecretError) as exc:
        _logger.warning("%s: %s", exc.code, exc.message)
    return store


def _trusted_paths(settings: Settings | None) -> tuple[str, ...]:
    paths: set[str] = set()
    configured = os.environ.get("MSJ_CONFIG_FILE")
    if configured:
        path = Path(configured).expanduser()
        paths.update((str(path), str(path.resolve())))
    if settings is not None:
        if settings.config_file is not None:
            path = settings.config_file.expanduser()
            paths.update((str(path), str(path.resolve())))
        data_dir = settings.data_dir.expanduser().resolve()
        paths.update((str(data_dir), str(data_dir / "journal.db"), str(data_dir / "token.secret")))
    return tuple(sorted(paths))


def _run_batch(settings: Settings, trigger: str = "manual") -> dict[str, Any]:
    store = _store(settings)
    embedding = EmbeddingService(store, settings)
    coordinator = BatchCoordinator(store, Runner(store, settings, embedding=embedding))
    if trigger == "schedule":
        coordinator.heartbeat_worker()
    return coordinator.run_sync("schedule" if trigger == "schedule" else "manual")


@contextmanager
def _graceful_shutdown(stop: threading.Event) -> Generator[None]:
    """Turn the first SIGTERM or SIGINT into a stop request; a second one interrupts.

    A stop request lets the running job finish, ends the batch before the next claim,
    releases its lease, and leaves the worker loop, so the interpreter shuts down normally
    (joblib/loky release their semaphores instead of leaking them to the resource tracker).
    """

    def request_stop(signum: int, _frame: FrameType | None) -> None:
        if stop.is_set():
            raise KeyboardInterrupt
        _logger.info(
            "%s received; finishing the current job, then exiting (send again to abort)",
            signal.Signals(signum).name,
        )
        stop.set()

    signals = [signal.SIGTERM]
    # A process started with SIGINT ignored (nohup, background job) keeps ignoring it.
    if signal.getsignal(signal.SIGINT) is not signal.SIG_IGN:
        signals.append(signal.SIGINT)
    previous = {signum: signal.signal(signum, request_stop) for signum in signals}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            _ = signal.signal(signum, handler)


def _worker(settings: Settings) -> None:
    wake = threading.Event()
    with _graceful_shutdown(wake):
        _worker_loop(settings, wake)
    _logger.info("Worker stopped")


def _worker_loop(settings: Settings, wake: threading.Event) -> None:  # noqa: C901, PLR0915
    store = _store(settings)
    embedding = EmbeddingService(store, settings)
    runner = Runner(store, settings, embedding=embedding, stop=wake)
    coordinator = BatchCoordinator(store, runner)
    tick_s = settings.worker_tick_s
    next_schedule = time.monotonic()
    schedule_pending = False
    while not wake.is_set():
        now = time.monotonic()
        if now >= next_schedule:
            schedule_pending = True
            next_schedule = now + settings.batch_interval_s
        try:
            request_at = coordinator.pending_request_at()
            requested = request_at is not None
        except Exception as exc:  # noqa: BLE001 - the worker outlives transient storage errors
            _logger.warning(
                "Worker request check failed: %s",
                safe_exception(exc, trusted_paths=_trusted_paths(settings)),
            )
            _ = wake.wait(tick_s)
            continue
        retry_at: float | None = None
        if schedule_pending or requested:
            try:
                coordinator.heartbeat_worker(
                    datetime.fromtimestamp(
                        time.time() + max(0.0, next_schedule - time.monotonic()), UTC
                    ).isoformat(),
                    observed_request_at=request_at,
                )
            except Exception as exc:  # noqa: BLE001 - a missed heartbeat must not stop work
                _logger.warning(
                    "Worker heartbeat update failed: %s",
                    safe_exception(exc, trusted_paths=_trusted_paths(settings)),
                )
            try:
                if requested:
                    result = coordinator.run_requested()
                else:
                    result = coordinator.run_sync("schedule", worker=True)
                if result is not None:
                    _logger.info("Batch finished: %s", result)
                next_schedule = time.monotonic() + settings.batch_interval_s
                schedule_pending = False
            except Conflict as exc:
                _logger.info(
                    "Batch deferred while another owner holds lease (%s)",
                    safe_exception(exc, trusted_paths=_trusted_paths(settings)),
                )
                # Recheck ownership every tick so a released lease is picked up promptly.
                retry_at = time.monotonic() + tick_s
            except Exception as exc:  # noqa: BLE001 - one failed batch must not stop the worker
                _logger.error(
                    "Scheduled batch failed: %s",
                    safe_exception(exc, trusted_paths=_trusted_paths(settings)),
                )
                next_schedule = time.monotonic() + settings.batch_interval_s
                schedule_pending = False
        now = time.monotonic()
        next_run = min(next_schedule, retry_at) if retry_at is not None else next_schedule
        next_run_at = time.time() + max(0.0, next_run - now)
        try:
            coordinator.heartbeat_worker(
                datetime.fromtimestamp(next_run_at, UTC).isoformat(),
                observed_request_at=request_at,
            )
        except Exception as exc:  # noqa: BLE001 - a missed heartbeat must not stop work
            _logger.warning(
                "Worker heartbeat update failed: %s",
                safe_exception(exc, trusted_paths=_trusted_paths(settings)),
            )
        delay = min(tick_s, max(0.0, next_run - now))
        _ = wake.wait(delay)


def _quiet_service_logs() -> None:
    """Keep service logs to one line per event: no per-request HTTP or progress-bar output."""
    # Progress bars from huggingface_hub, transformers and sentence-transformers are for
    # terminals; in a service log they are noise. setdefault keeps an operator's override.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TQDM_DISABLE", "1")
    # httpx logs every RunPod status poll at INFO; providers log one line per job instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _dispatch(command: str, args: argparse.Namespace, settings: Settings) -> None:
    match command:
        case "serve":
            store = _store(settings)
            if store.token_hash() == "reissue-required":
                _logger.warning(
                    "TOKEN_REISSUE_REQUIRED: token must be re-issued: run `msj create-token`."
                )
            uvicorn.run(
                create_app(settings),
                host=args.host,
                port=args.port,
                log_level="info",
                access_log=False,
                forwarded_allow_ips=settings.forwarded_allow_ips,
            )
        case "worker":
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
            )
            _worker(settings)
        case "run-batch":
            result = _run_batch(settings)
            print(result)
        case "migrate":
            store = _store(settings)
            print(f"Database ready at {settings.data_dir.expanduser() / 'journal.db'}")
            if store.token_hash() == "reissue-required":
                print("TOKEN_REISSUE_REQUIRED: token must be re-issued: run `msj create-token`.")
        case "create-token":
            store = _store(settings)
            print(create_token(store))
        case "embedding-download":
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found,unused-ignore]

            _ = SentenceTransformer(
                settings.embedding.model,
                device=settings.embedding.device,
                revision=settings.embedding.revision,
            )
            print(
                f"Downloaded {settings.embedding.model} at revision {settings.embedding.revision}."
            )
        case _:
            raise AssertionError(f"Unhandled command: {command}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"serve", "worker"}:
        _quiet_service_logs()
    settings: Settings | None = None
    try:
        settings = load_settings()
        _dispatch(args.command, args, settings)
    except KeyboardInterrupt:
        return 130
    except TokenReissueRequired:
        _logger.error("TOKEN_REISSUE_REQUIRED: token must be re-issued: run `msj create-token`.")
        return 1
    except Exception as exc:  # noqa: BLE001 - the command boundary turns failures into exit 1
        _logger.error(
            "Command failed: %s",
            safe_exception(exc, trusted_paths=_trusted_paths(settings)),
        )
        return 1
    return 0
