"""Batch lease and compilation behavior through the SQLite store."""

import json
import time
from pathlib import Path
from typing import Any

import pytest

from msj.batch import BatchCoordinator, queue_status
from msj.compilations import CompilationService
from msj.config import TASKS, EmbeddingSettings, Settings, TaskKind, TaskRoute
from msj.pipeline import Runner
from msj.providers import Generation, SafeProviderError
from msj.storage import Conflict, Store


def _store(path: Path) -> Store:
    settings = Settings(
        data_dir=path,
        embedding=EmbeddingSettings(provider="fake"),
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    return store


class RecordingProvider:
    def __init__(self) -> None:
        self.compile_source: str | None = None

    def generate(
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        *,
        candidate: str | None = None,
        audio: bytes | None = None,
        messages: Any = None,
    ) -> Generation:
        del candidate, audio, messages
        if route.expect_model is not None and route.expect_model != "fake":
            raise SafeProviderError("Stale worker or wrong model: provider metadata did not match")
        if kind == "transcribe":
            return Generation("I deployed the Aurora service migration.", "fake")
        if kind == "compile":
            self.compile_source = source
            return Generation(
                json.dumps(
                    {
                        "resume_bullets": [],
                        "star_stories": [],
                        "technical_topics": [],
                        "open_threads": [],
                    }
                ),
                "fake",
            )
        if kind == "review":
            return Generation('{"unsupported_claims": []}', "fake")
        if kind == "extract":
            return Generation(
                json.dumps(
                    {
                        "projects": [],
                        "skills_technologies": [],
                        "accomplishments": [],
                        "people_roles": [],
                        "star_fragments": [],
                        "tags": [],
                    }
                ),
                "fake",
            )
        return Generation(source, "fake")


def test_runner_and_worker_share_sqlite_batch_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runner = Runner(store, store.settings)
    coordinator = BatchCoordinator(store, runner)
    batch_id = coordinator.acquire("manual")
    assert batch_id is not None
    with pytest.raises(Conflict, match="already running"):
        runner.run_batch()
    coordinator.release(batch_id, {"done": 0, "failed": 0, "discarded": 0})


def test_independent_store_instances_observe_one_batch_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    other_store = Store(store.settings)
    first = BatchCoordinator(store, Runner(store, store.settings))
    second = BatchCoordinator(other_store, Runner(other_store, store.settings))
    batch_id = first.acquire("manual")
    assert batch_id is not None
    assert second.acquire("schedule") is None
    first.release(batch_id, None)


def test_expired_batch_lease_can_be_recovered_and_stale_heartbeat_is_reported(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    coordinator = BatchCoordinator(store, Runner(store, store.settings))
    stale_seen = "2000-01-01T00:00:00+00:00"
    with store.connect() as db:
        _ = db.execute(
            "UPDATE batch_state SET batch_id=?,started_at=?,trigger='manual',lease_until=?,"
            "worker_last_seen_at=?,worker_next_run_at=? WHERE id=1",
            ("expired", stale_seen, time.time() - 10, stale_seen, stale_seen),
        )
    recovered = coordinator.acquire("schedule")
    assert recovered is not None
    status = queue_status(store)
    assert status["batch"]["running"] is True
    assert status["worker"]["last_seen_at"] == stale_seen
    assert status["worker"]["next_run_at"] is not None
    coordinator.release(recovered, None)


def test_compilation_job_publishes_schema_valid_result(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create_entry(text="Built the Atlas import pipeline.", entry_date="2026-11-01")
    second = store.create_entry(text="Investigated a slow database query.", entry_date="2026-11-02")
    runner = Runner(store, store.settings)
    assert runner.run_batch()["failed"] == 0

    service = CompilationService(store)
    compilation = service.create(
        [first["id"], second["id"]], "Prepare interview examples", "November 2026"
    )
    assert compilation["status"] == "queued"
    result = runner.run_batch()
    assert result["failed"] == 0
    completed = service.get(compilation["id"])
    assert completed["status"] == "done"
    assert completed["entry_ids"] == [first["id"], second["id"]]
    assert completed["model"] == "fake"
    assert completed["prompt_sha256"]
    assert completed["result"] == {
        "resume_bullets": [],
        "star_stories": [],
        "technical_topics": [],
        "open_threads": [],
    }


def test_compilation_created_before_audio_processing_snapshots_final_transcript(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    entry = store.create_entry(
        audio=b"synthetic audio",
        extension="wav",
        title="Aurora rollout",
        entry_date="2026-11-03",
    )
    service = CompilationService(store)
    compilation = service.create([entry["id"]], "Resume bullets", "November 2026")
    with store.connect() as db:
        row = db.execute(
            "SELECT source_content FROM compilations WHERE id=?", (compilation["id"],)
        ).fetchone()
    assert row["source_content"] == "null"

    provider = RecordingProvider()
    runner = Runner(store, store.settings, provider=provider)
    result = runner.run_batch()
    assert result["failed"] == 0
    assert provider.compile_source is not None
    source = json.loads(provider.compile_source)
    assert source["entries"][0]["text"] == "I deployed the Aurora service migration."
    completed = service.get(compilation["id"])
    assert completed["status"] == "done"
    with store.connect() as db:
        saved = json.loads(
            db.execute(
                "SELECT source_content FROM compilations WHERE id=?", (compilation["id"],)
            ).fetchone()[0]
        )
    assert saved == source["entries"]


def test_failed_entry_processing_keeps_compilation_queued_and_unmaterialized(
    tmp_path: Path,
) -> None:
    routes = {kind: TaskRoute(provider="fake") for kind in TASKS}
    routes["cleanup"] = TaskRoute(provider="fake", expect_model="expected-model")
    settings = Settings(
        data_dir=tmp_path,
        embedding=EmbeddingSettings(provider="fake"),
        tasks=routes,
        max_attempts=1,
    )
    store = Store(settings)
    store.migrate()
    entry = store.create_entry(text="Incomplete processing must block compile.")
    service = CompilationService(store)
    compilation = service.create([entry["id"]], "Resume bullets", "November 2026")
    runner = Runner(store, settings, provider=RecordingProvider())
    assert runner.run_batch()["failed"] == 1
    current = service.get(compilation["id"])
    assert current["status"] == "queued"
    with store.connect() as db:
        row = db.execute(
            "SELECT c.source_content,j.status,j.id FROM compilations c "
            "JOIN jobs j ON j.compilation_id=c.id WHERE c.id=?",
            (compilation["id"],),
        ).fetchone()
        assert row["source_content"] == "null"
        assert row["status"] == "queued"
        blocked = store._job_views(
            db, [db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()]
        )[0]
    assert blocked["blocked_by"]


def test_revision_before_compilation_snapshot_uses_latest_entry_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.create_entry(text="Initial entry wording.")
    service = CompilationService(store)
    compilation = service.create([entry["id"]], "Resume bullets", "November 2026")
    revised = store.patch_entry(entry["id"], {"text": "Revised accomplishment wording."})
    assert revised["revision"] == 2

    provider = RecordingProvider()
    runner = Runner(store, store.settings, provider=provider)
    assert runner.run_batch()["failed"] == 0
    assert provider.compile_source is not None
    compiled_entries = json.loads(provider.compile_source)["entries"]
    assert compiled_entries[0]["text"] == "Revised accomplishment wording."
    assert service.get(compilation["id"])["status"] == "done"
