"""Command line entry points for the journal service."""

import argparse
import logging
import os
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
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
        logging.warning("%s: %s", exc.code, exc.message)
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


def _worker(settings: Settings) -> None:
    store = _store(settings)
    embedding = EmbeddingService(store, settings)
    runner = Runner(store, settings, embedding=embedding)
    coordinator = BatchCoordinator(store, runner)
    tick_s = settings.worker_tick_s
    next_schedule = time.monotonic()
    wake = threading.Event()
    schedule_pending = False
    while not wake.is_set():
        now = time.monotonic()
        if now >= next_schedule:
            schedule_pending = True
            next_schedule = now + settings.batch_interval_s
        try:
            request_at = coordinator.pending_request_at()
            requested = request_at is not None
        except Exception as exc:
            logging.warning(
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
            except Exception as exc:
                logging.warning(
                    "Worker heartbeat update failed: %s",
                    safe_exception(exc, trusted_paths=_trusted_paths(settings)),
                )
            try:
                if requested:
                    result = coordinator.run_requested()
                else:
                    result = coordinator.run_sync("schedule", worker=True)
                if result is not None:
                    logging.info("Batch finished: %s", result)
                next_schedule = time.monotonic() + settings.batch_interval_s
                schedule_pending = False
            except Conflict as exc:
                logging.info(
                    "Batch deferred while another owner holds lease (%s)",
                    safe_exception(exc, trusted_paths=_trusted_paths(settings)),
                )
                # Recheck ownership every tick so a released lease is picked up promptly.
                retry_at = time.monotonic() + tick_s
            except Exception as exc:
                logging.error(
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
        except Exception as exc:
            logging.warning(
                "Worker heartbeat update failed: %s",
                safe_exception(exc, trusted_paths=_trusted_paths(settings)),
            )
        delay = min(tick_s, max(0.0, next_run - now))
        _ = wake.wait(delay)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings: Settings | None = None
    try:
        settings = load_settings()
        match args.command:
            case "serve":
                store = _store(settings)
                if store.token_hash() == "reissue-required":
                    logging.warning(
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
                    print(
                        "TOKEN_REISSUE_REQUIRED: token must be re-issued: run `msj create-token`."
                    )
                del store
            case "create-token":
                store = _store(settings)
                print(create_token(store))
            case "embedding-download":
                from sentence_transformers import SentenceTransformer

                _ = SentenceTransformer(
                    settings.embedding.model,
                    device=settings.embedding.device,
                    revision=settings.embedding.revision,
                )
                print(
                    f"Downloaded {settings.embedding.model} at revision "
                    f"{settings.embedding.revision}."
                )
            case _:
                raise AssertionError(f"Unhandled command: {args.command}")
    except KeyboardInterrupt:
        return 130
    except TokenReissueRequired:
        logging.error("TOKEN_REISSUE_REQUIRED: token must be re-issued: run `msj create-token`.")
        return 1
    except Exception as exc:
        logging.error(
            "Command failed: %s",
            safe_exception(exc, trusted_paths=_trusted_paths(settings)),
        )
        return 1
    return 0
