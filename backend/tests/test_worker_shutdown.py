"""A stop request ends a batch between jobs and releases its lease."""

import threading
from pathlib import Path

from msj.batch import BatchCoordinator, queue_status
from msj.config import TASKS, Settings, TaskRoute
from msj.pipeline import Runner
from msj.storage import Store


def test_stop_request_ends_batch_after_current_job_and_releases_lease(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        embedding={"provider": "none"},
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    for text in ("First synthetic entry.", "Second synthetic entry."):
        _ = store.create_entry(text=text)
    stop = threading.Event()
    runner = Runner(store, settings, stop=stop)
    original_finish = store.finish_job
    finished: list[str] = []

    def finish_then_request_stop(job: dict[str, object], *args: object, **kwargs: object) -> bool:
        published = original_finish(job, *args, **kwargs)  # type: ignore[arg-type]
        finished.append(str(job["id"]))
        stop.set()
        return published

    store.finish_job = finish_then_request_stop  # type: ignore[method-assign]
    result = BatchCoordinator(store, runner).run_sync("manual")

    assert result["done"] == 1
    assert len(finished) == 1
    status = queue_status(store)
    assert status["batch"]["running"] is False
    assert status["batch"]["last_error"] is None
    assert status["counts"]["running"] == 0
    assert status["counts"]["queued"] > 0

    # The next batch resumes the remaining work.
    stop.clear()
    store.finish_job = original_finish  # type: ignore[method-assign]
    resumed = BatchCoordinator(store, Runner(store, settings)).run_sync("manual")
    assert resumed["done"] > 0
    assert resumed["failed"] == 0
