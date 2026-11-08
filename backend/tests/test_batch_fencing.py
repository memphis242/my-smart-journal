"""A replacement batch fences publication by an expired owner."""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from msj.batch import BatchCoordinator
from msj.compilations import CompilationService
from msj.config import TASKS, Settings, TaskKind, TaskRoute
from msj.embedding import EmbeddingService
from msj.pipeline import Runner
from msj.providers import Generation
from msj.storage import Conflict, Store


def test_replaced_batch_cannot_publish_inflight_provider_result(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    entry = store.create_entry(text="The only persisted source.")
    started = threading.Event()
    resume = threading.Event()

    class BlockingProvider:
        def generate(
            self, kind: TaskKind, route: TaskRoute, source: str, **kwargs: Any
        ) -> Generation:
            started.set()
            assert resume.wait(5)
            return Generation("A result from an expired batch.", "fake")

    runner = Runner(store, settings, BlockingProvider())
    replacement = BatchCoordinator(store, Runner(store, settings))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runner.run_batch)
        replacement_id: str | None = None
        try:
            assert started.wait(5)
            with store.connect() as db:
                _ = db.execute("UPDATE batch_state SET lease_until=0 WHERE id=1")
            replacement_id = replacement.acquire("manual")
            assert replacement_id is not None
        finally:
            resume.set()
        with pytest.raises(Conflict):
            _ = future.result(timeout=5)
        assert replacement_id is not None
        replacement.release(replacement_id, None)
    assert all(
        artifact["kind"] != "cleaned" for artifact in store.get_entry(entry["id"])["artifacts"]
    )


def test_entry_deletion_removes_compilation_source_and_cancels_job(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    entry = store.create_entry(text="Private source must disappear on deletion.")
    compilation = CompilationService(store).create([entry["id"]], "Interview", "November")
    store.delete_entry(entry["id"])
    with store.connect() as db:
        assert db.execute(
            "SELECT 1 FROM compilations WHERE id=?", (compilation["id"],)
        ).fetchone() is None
    assert all(job["kind"] != "compile" for job in store.list_jobs())


@pytest.mark.parametrize("kind", ["cleanup", "embed", "compile"])
def test_expired_owner_cannot_publish_or_record_failure(tmp_path: Path, kind: str) -> None:
    settings = Settings(
        data_dir=tmp_path,
        embedding={"provider": "fake"},
        tasks={task: TaskRoute(provider="fake") for task in TASKS},
    )
    store = Store(settings)
    store.migrate()
    entry = store.create_entry(text="A synthetic journal entry for fencing.")
    runner = Runner(store, settings)
    if kind != "cleanup":
        assert runner.run_batch()["failed"] == 0
    if kind == "compile":
        _ = CompilationService(store).create([entry["id"]], "Interview", "November")
    target = next(job for job in store.list_jobs() if job["kind"] == kind)
    if kind == "embed":
        with store.connect() as db:
            _ = db.execute("UPDATE jobs SET status='queued' WHERE id=?", (target["id"],))
    coordinator = BatchCoordinator(store, runner)
    batch_id = coordinator.acquire("manual")
    assert batch_id is not None
    job = store.claim({target["id"]}, batch_id=batch_id)
    assert job is not None
    with store.connect() as db:
        _ = db.execute("UPDATE batch_state SET lease_until=0 WHERE id=1")
    if kind == "embed":
        assert EmbeddingService(store, settings).process(job) is False
    else:
        assert store.finish_job(job, "{}", "fake", "digest") is False
    assert store.fail_job(job, "This failure belongs to an expired batch") is False
    with pytest.raises(Conflict):
        _ = store.claim({target["id"]}, batch_id=batch_id)
    with pytest.raises(Conflict):
        coordinator.renew(batch_id)
    current = next(item for item in store.list_jobs() if item["id"] == job["id"])
    assert current["status"] == "running"
