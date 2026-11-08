"""Persistence and atomic queue transitions for bounded review sessions."""

import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from msj.config import Settings, TaskRoute
from msj.storage import Store


def make_store(tmp_path: Path, **options: Any) -> tuple[Store, Settings]:
    defaults: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "tasks": {
            kind: TaskRoute(model="fake")
            for kind in ("transcribe", "cleanup", "review", "summarize", "extract")
        },
    }
    defaults.update(options)
    settings = Settings(**defaults)
    store = Store(settings)
    store.migrate()
    return store, settings


def find_job(store: Store, entry_id: str, kind: str, target: str, turn: int) -> dict[str, Any]:
    return next(
        job
        for job in store.list_jobs()
        if job["entry_id"] == entry_id
        and job["kind"] == kind
        and job["target"] == target
        and job["turn"] == turn
    )


def take(store: Store, entry_id: str, kind: str, target: str, turn: int) -> dict[str, Any]:
    claimed = store.claim({find_job(store, entry_id, kind, target, turn)["id"]})
    assert claimed is not None
    return claimed


def test_review_revision_queue_pins_claims_and_versions_candidates(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path, review={"max_turns": 2})
    entry = store.create_entry(text="The original source.")
    cleanup = take(store, entry["id"], "cleanup", "cleanup", 0)
    assert store.finish_job(cleanup, "First candidate.", "phi", "prompt")

    review = take(store, entry["id"], "review", "cleanup", 1)
    assert store.finish_job(
        review,
        '{"unsupported_claims":["First claim"]}',
        "gemma",
        "review-sha",
        unsupported_claims=("First claim",),
    )
    revise = take(store, entry["id"], "revise", "cleanup", 1)
    source, candidate, audio = store.job_inputs(revise)
    assert (source, candidate, audio) == ("The original source.", "First candidate.", None)
    assert store.revision_claims(revise) == ("First claim",)
    assert store.finish_job(revise, "Corrected candidate.", "phi", "prompt-revise")

    review2 = take(store, entry["id"], "review", "cleanup", 2)
    assert review2["input_artifact_id"] != review["input_artifact_id"]
    with store.connect() as db:
        _ = db.execute(
            "UPDATE artifacts SET needs_attention=1 WHERE id=?",
            (review2["input_artifact_id"],),
        )
    assert store.finish_job(
        review2,
        '{"unsupported_claims":[]}',
        "gemma",
        "review-sha",
        unsupported_claims=(),
    )

    entry_view = store.get_entry(entry["id"])
    session = entry_view["review_sessions"]["cleanup"]
    assert session["status"] == "accepted"
    assert session["turns_used"] == 2
    assert session["remaining_claims"] == []
    assert session["final_artifact"]["needs_attention"] is False
    assert [item["turn"] for item in session["history"]] == [1, 2]
    versions = [
        item
        for item in store.get_entry(entry["id"], history=True)["artifacts"]
        if item["kind"] == "cleaned"
    ]
    assert len(versions) == 2
    assert versions[0]["superseded_by"] == versions[1]["id"]
    assert session["final_artifact"]["id"] == versions[1]["id"]
    assert session["history"][0]["reviewed_artifact_id"] == versions[0]["id"]
    store.delete_entry(entry["id"])
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM review_sessions").fetchone()[0] == 0


def test_review_limit_persists_remaining_claims_as_attention(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path, review={"max_turns": 1})
    entry = store.create_entry(text="Source.")
    producer = take(store, entry["id"], "cleanup", "cleanup", 0)
    assert store.finish_job(producer, "Candidate.", "phi", "sha")
    review = take(store, entry["id"], "review", "cleanup", 1)
    assert store.finish_job(
        review,
        '{"unsupported_claims":["claim"]}',
        "gemma",
        "sha",
        unsupported_claims=("claim",),
    )
    session = store.get_entry(entry["id"])["review_sessions"]["cleanup"]
    assert session["status"] == "needs_attention"
    assert session["remaining_claims"] == ["claim"]
    assert session["final_artifact"]["needs_attention"] is True
    assert not any(job["kind"] == "revise" for job in store.list_jobs())


def test_disabled_review_session_records_final_producer_artifact(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path, review={"enabled_for": ("summarize",)})
    entry = store.create_entry(text="Source.")
    producer = take(store, entry["id"], "cleanup", "cleanup", 0)
    assert store.finish_job(producer, "Cleaned.", "phi", "sha")
    session = store.get_entry(entry["id"])["review_sessions"]["cleanup"]
    assert session["status"] == "disabled"
    assert session["final_artifact"]["content"] == "Cleaned."
    assert not any(
        job["kind"] == "review" and job["target"] == "cleanup" for job in store.list_jobs()
    )


def test_migration_preserves_legacy_rows_and_creates_review_session(tmp_path: Path) -> None:
    data_dir = tmp_path / "legacy"
    data_dir.mkdir()
    database = data_dir / "journal.db"
    connection = sqlite3.connect(database)
    migrations = Path(__file__).parents[1] / "src/msj/resources/migrations"
    for name in ("001_initial.sql", "002_file_deletions.sql", "003_superseded_jobs.sql"):
        connection.executescript((migrations / name).read_text(encoding="utf-8"))
    connection.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
    connection.executemany(
        "INSERT INTO schema_migrations VALUES (?)",
        [
            (name,)
            for name in ("001_initial.sql", "002_file_deletions.sql", "003_superseded_jobs.sql")
        ],
    )
    entry_id = str(uuid4())
    cleanup_id, review_id, summary_id, extract_id = (str(uuid4()) for _ in range(4))
    raw_id, clean_id, review_artifact_id, summary_artifact_id, extract_artifact_id = (
        str(uuid4()) for _ in range(5)
    )
    created = "2025-01-01T00:00:00+00:00"
    connection.execute(
        "INSERT INTO entries (id,created_at,entry_date,source) VALUES (?,?,?,'text')",
        (entry_id, created, "2025-01-01"),
    )
    for artifact_id, kind, content in (
        (raw_id, "user_text", "source"),
        (clean_id, "cleaned", "cleaned"),
        (review_artifact_id, "review", '{"unsupported_claims":[]}'),
        (summary_artifact_id, "summary", "summary"),
        (extract_artifact_id, "extraction", "{}"),
    ):
        connection.execute(
            "INSERT INTO artifacts (id,entry_id,kind,content,created_at,revision) "
            "VALUES (?,?,?,?,?,1)",
            (artifact_id, entry_id, kind, content, created),
        )
    job_ids = (
        (cleanup_id, "cleanup", "done"),
        (review_id, "review", "done"),
        (summary_id, "summarize", "done"),
        (extract_id, "extract", "done"),
    )
    for job_id, kind, status in job_ids:
        connection.execute(
            "INSERT INTO jobs (id,entry_id,kind,status,not_before,created_at,revision) "
            "VALUES (?,?,?,?,?,?,1)",
            (job_id, entry_id, kind, status, time.time(), created),
        )
    connection.execute("INSERT INTO job_dependencies VALUES (?,?)", (review_id, cleanup_id))
    connection.execute("INSERT INTO job_dependencies VALUES (?,?)", (summary_id, cleanup_id))
    connection.execute("INSERT INTO job_dependencies VALUES (?,?)", (extract_id, cleanup_id))
    connection.commit()
    connection.close()

    settings = Settings(
        data_dir=data_dir,
        tasks={
            kind: TaskRoute(model="fake")
            for kind in ("transcribe", "cleanup", "review", "summarize", "extract")
        },
    )
    store = Store(settings)
    store.migrate()
    with store.connect() as db:
        content = db.execute("SELECT content FROM artifacts WHERE id=?", (clean_id,)).fetchone()[0]
        assert content == "cleaned"
        assert (
            db.execute(
                "SELECT reviewed_artifact_id FROM artifacts WHERE id=?",
                (review_artifact_id,),
            ).fetchone()[0]
            == clean_id
        )
        legacy = db.execute(
            "SELECT status,target,turn FROM jobs WHERE id=?", (review_id,)
        ).fetchone()
        assert tuple(legacy) == ("done", "cleanup", 0)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    jobs = store.list_jobs()
    assert any(
        job["kind"] == "review"
        and job["target"] == "cleanup"
        and job["turn"] == 1
        and job["status"] == "queued"
        for job in jobs
    )
    for target in ("summarize", "extract"):
        followup = find_job(store, entry_id, "review", target, 1)
        assert followup["status"] == "queued"
        source, candidate, _ = store.job_inputs(followup)
        assert source == "cleaned"
        assert candidate in {"summary", "{}"}
        assert store.claim({followup["id"]}) is None
        with store.connect() as db:
            dependency = db.execute(
                "SELECT 1 FROM job_dependencies WHERE job_id=? AND depends_on IN "
                "(SELECT id FROM jobs WHERE entry_id=? AND target='cleanup' AND kind='review' "
                "AND turn=1)",
                (followup["id"], entry_id),
            ).fetchone()
        assert dependency is not None


def test_historical_attention_does_not_change_current_entry_status(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    entry = store.create_entry(text="Original source")
    producer = take(store, entry["id"], "cleanup", "cleanup", 0)
    assert store.finish_job(producer, "Unsupported candidate", "phi", "producer-sha")
    review = take(store, entry["id"], "review", "cleanup", 1)
    with store.connect() as db:
        _ = db.execute(
            "UPDATE artifacts SET needs_attention=1 WHERE id=?", (review["input_artifact_id"],)
        )
    assert store.finish_job(
        review,
        '{"unsupported_claims":["unsupported"]}',
        "gemma",
        "review-sha",
        unsupported_claims=("unsupported",),
    )
    revise = take(store, entry["id"], "revise", "cleanup", 1)
    assert store.finish_job(revise, "Supported correction", "phi", "revision-sha")
    final_review = take(store, entry["id"], "review", "cleanup", 2)
    assert store.finish_job(
        final_review, '{"unsupported_claims":[]}', "gemma", "review-sha", unsupported_claims=()
    )
    assert store.get_entry(entry["id"])["needs_attention"] is False
    historical = store.get_entry(entry["id"], history=True)
    assert historical["review_sessions"]["cleanup"]["status"] == "accepted"
    assert historical["needs_attention"] is False
    old = next(
        artifact
        for artifact in historical["artifacts"]
        if artifact["id"] == review["input_artifact_id"]
    )
    assert old["needs_attention"] is True


@pytest.mark.parametrize("attempts", [1, 3])
def test_upgrade_fences_legacy_claims_and_new_workers_resume_review(
    tmp_path: Path,
    attempts: int,
) -> None:
    from msj.pipeline import Runner

    directory = tmp_path / "upgrade"
    directory.mkdir()
    resources = Path(__file__).parents[1] / "src/msj/resources/migrations"
    with sqlite3.connect(directory / "journal.db") as db:
        _ = db.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
        for name in ("001_initial.sql", "002_file_deletions.sql", "003_superseded_jobs.sql"):
            db.executescript((resources / name).read_text())
            _ = db.execute("INSERT INTO schema_migrations VALUES (?)", (name,))
        _ = db.execute(
            "INSERT INTO entries(id,created_at,entry_date,source) "
            "VALUES ('entry','now','2026-11-06','text')"
        )
        _ = db.execute(
            "INSERT INTO artifacts(id,entry_id,kind,content,created_at,revision) "
            "VALUES ('raw','entry','user_text','original source','now',1)"
        )
        for job_id, kind in (
            ("cleanup-job", "cleanup"),
            ("summary-job", "summarize"),
            ("extract-job", "extract"),
        ):
            _ = db.execute(
                "INSERT INTO jobs(id,entry_id,kind,not_before,created_at,revision) "
                "VALUES (?,'entry',?,0,'now',1)",
                (job_id, kind),
            )
        _ = db.execute(
            "UPDATE jobs SET status='running',attempts=?,claim_token='old-token',"
            "lease_until=? WHERE id='cleanup-job'",
            (attempts, time.time() + 600),
        )
        _ = db.executemany(
            "INSERT INTO job_dependencies VALUES (?,'cleanup-job')",
            [("summary-job",), ("extract-job",)],
        )
    settings = Settings(
        data_dir=directory,
        tasks={
            kind: TaskRoute(provider="fake")
            for kind in ("transcribe", "cleanup", "review", "summarize", "extract")
        },
    )
    store = Store(settings)
    store.migrate()
    old_claim = {
        "id": "cleanup-job",
        "entry_id": "entry",
        "kind": "cleanup",
        "target": "cleanup",
        "turn": 0,
        "revision": 1,
        "claim_token": "old-token",
    }
    assert store.finish_job(old_claim, "stale producer output", "old-model", "sha") is False
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as db:
            _ = db.execute(
                "UPDATE jobs SET status='running',claim_token='legacy-reclaim',"
                "lease_until=? WHERE id='cleanup-job'",
                (time.time() + 600,),
            )
    result = Runner(store, settings).run_batch()
    if attempts == settings.max_attempts:
        assert result["done"] == 0
        exhausted = store.list_jobs(status="failed")
        assert exhausted[0]["id"] == "cleanup-job"
        assert exhausted[0]["attempts"] == attempts
        _ = store.retry_job("cleanup-job")
        assert Runner(store, settings).run_batch()["done"] == 6
    else:
        assert result["done"] == 6
    entry = store.get_entry("entry", history=True)
    assert all(session["status"] == "accepted" for session in entry["review_sessions"].values())
    assert not any(
        artifact["content"] == "stale producer output" for artifact in entry["artifacts"]
    )


def test_session_retry_keeps_original_route_and_policy_after_restart(tmp_path: Path) -> None:
    import httpx

    from msj.config import ReviewSettings
    from msj.pipeline import Runner
    from msj.providers import Generation, InferenceProvider

    routes = {
        kind: TaskRoute(provider="fake")
        for kind in ("transcribe", "cleanup", "review", "summarize", "extract")
    }
    original_route = TaskRoute(
        provider="fake", model="original-phi", endpoint_id="original-endpoint"
    )
    routes["cleanup"] = original_route
    store, settings = make_store(tmp_path, tasks=routes)
    entry = store.create_entry(text="Original source")
    producer = take(store, entry["id"], "cleanup", "cleanup", 0)
    assert store.finish_job(producer, "Candidate", "original-phi", "sha")
    review = take(store, entry["id"], "review", "cleanup", 1)
    assert store.finish_job(
        review, '{"unsupported_claims":["claim"]}', "fake", "sha", unsupported_claims=("claim",)
    )
    revise = take(store, entry["id"], "revise", "cleanup", 1)
    assert store.fail_job(revise, "temporary failure", retryable=False)
    changed_routes = routes | {"cleanup": TaskRoute(provider="fake", model="changed-model")}
    changed = settings.model_copy(
        update={"tasks": changed_routes, "review": ReviewSettings(max_turns=1, enabled_for=())}
    )
    reopened = Store(changed)
    reopened.migrate()
    _ = reopened.retry_job(revise["id"])
    observed: list[TaskRoute] = []
    with httpx.Client() as http:
        delegate = InferenceProvider(changed, http)

        class RecordingFake:
            def generate(
                self,
                kind: Any,
                route: TaskRoute,
                source: str,
                *,
                candidate: str | None = None,
                audio: bytes | None = None,
                messages: Any = None,
            ) -> Generation:
                if messages is not None:
                    observed.append(route)
                    return Generation("Corrected candidate", route.model)
                return delegate.generate(kind, route, source, candidate=candidate, audio=audio)

        assert Runner(reopened, changed, RecordingFake()).run_batch()["failed"] == 0
    assert observed == [original_route]
    session = reopened.get_entry(entry["id"])["review_sessions"]["cleanup"]
    assert session["status"] == "accepted"
    assert session["turns_used"] == 2
    assert len(session["history"]) == 2
    assert session["final_artifact"]["model"] == "original-phi"
