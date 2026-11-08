"""Command line entry points for the journal service."""

import argparse
import logging
import time
from collections.abc import Sequence
from typing import Any

import uvicorn

from .api import create_app
from .auth import create_token
from .config import Settings, load_settings
from .pipeline import Runner
from .storage import Store


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
    return parser


def _store(settings: Settings) -> Store:
    store = Store(settings)
    store.migrate()
    return store


def _run_batch(settings: Settings) -> dict[str, Any]:
    store = _store(settings)
    return Runner(store, settings).run_batch()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = load_settings()
        match args.command:
            case "serve":
                uvicorn.run(
                    create_app(settings),
                    host=args.host,
                    port=args.port,
                    log_level="info",
                    access_log=False,
                )
            case "worker":
                logging.basicConfig(
                    level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                )
                while True:
                    try:
                        result = _run_batch(settings)
                        logging.info("Batch finished: %s", result)
                    except Exception:
                        logging.exception(
                            "Batch failed; inspect persisted job errors and service logs"
                        )
                    time.sleep(settings.batch_interval_s)
            case "run-batch":
                result = _run_batch(settings)
                print(result)
            case "migrate":
                store = _store(settings)
                print(f"Database ready at {settings.data_dir.expanduser() / 'journal.db'}")
                del store
            case "create-token":
                store = _store(settings)
                print(create_token(store))
            case _:
                raise AssertionError(f"Unhandled command: {args.command}")
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.error("%s", exc)
        return 1
    return 0
