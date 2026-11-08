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
from .auth import TokenReissueRequired, create_token
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


def _store(settings: Settings, *, allow_token_reissue: bool = False) -> Store:
    store = Store(settings)
    store.migrate()
    if not allow_token_reissue and store.token_hash() == "reissue-required":
        raise TokenReissueRequired("Bearer token must be re-issued: run `msj create-token`.")
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
    retry_after = 0.0
    while not wake.is_set():
        now = time.monotonic()
        scheduled = now >= next_schedule
        try:
            requested = coordinator.has_pending_request()
        except Exception as exc:
            logging.warning(
                "Worker request check failed: %s",
                safe_exception(exc, trusted_paths=_trusted_paths(settings)),
            )
            wake.wait(tick_s)
            continue
        should_run = (scheduled or requested) and now >= retry_after
        delay = min(tick_s, max(0.0, next_schedule - now))
        if not should_run and now < retry_after:
            delay = min(tick_s, retry_after - now)
        next_wake_at = time.time() + delay
        try:
            coordinator.heartbeat_worker(datetime.fromtimestamp(next_wake_at, UTC).isoformat())
        except Exception as exc:
            logging.warning(
                "Worker heartbeat update failed: %s",
                safe_exception(exc, trusted_paths=_trusted_paths(settings)),
            )
            wake.wait(tick_s)
            continue
        if should_run:
            try:
                if requested:
                    result = coordinator.run_requested()
                else:
                    result = coordinator.run_sync("schedule", worker=True)
                if result is not None:
                    logging.info("Batch finished: %s", result)
                next_schedule = time.monotonic() + settings.batch_interval_s
                retry_after = 0.0
            except Conflict as exc:
                remaining = coordinator.lease_remaining_s()
                logging.info(
                    "Batch deferred while another owner holds lease (%s)",
                    safe_exception(exc, trusted_paths=_trusted_paths(settings)),
                )
                retry_after = time.monotonic() + remaining
            except Exception as exc:
                logging.error(
                    "Scheduled batch failed: %s",
                    safe_exception(exc, trusted_paths=_trusted_paths(settings)),
                )
                next_schedule = time.monotonic() + settings.batch_interval_s
            continue
        wake.wait(delay)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings: Settings | None = None
    try:
        settings = load_settings()
        match args.command:
            case "serve":
                store = _store(settings, allow_token_reissue=True)
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
                store = _store(settings, allow_token_reissue=True)
                print(f"Database ready at {settings.data_dir.expanduser() / 'journal.db'}")
                if store.token_hash() == "reissue-required":
                    print(
                        "TOKEN_REISSUE_REQUIRED: token must be re-issued: run `msj create-token`."
                    )
                del store
            case "create-token":
                store = _store(settings, allow_token_reissue=True)
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
