"""Upgrade a populated review-session database without losing journal lineage."""

import json
import sqlite3
from importlib.resources import files
from pathlib import Path

from msj.config import TASKS, Settings, TaskRoute
from msj.pipeline import Runner
from msj.storage import Store


def test_previous_schema_keeps_data_dependencies_and_queues_embedding(tmp_path: Path) -> None:
    db = sqlite3.connect(tmp_path / "journal.db")
    resources = files("msj").joinpath("resources/migrations")
    with db:
        _ = db.execute("PRAGMA foreign_keys=ON")
        _ = db.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
        for resource in sorted(resources.iterdir(), key=lambda item: item.name):
            if resource.name[:3] > "004" or not resource.name.endswith(".sql"):
                continue
            _ = db.executescript(resource.read_text(encoding="utf-8"))
            _ = db.execute("INSERT INTO schema_migrations VALUES (?)", (resource.name,))
        _ = db.execute(
            "INSERT INTO entries(id,created_at,entry_date,title,tags,source) "
            "VALUES ('entry','2026-11-01T09:00:00Z','2026-11-01','Atlas','[\"Work\"]','text')"
        )
        for artifact_id, kind, content in (
            ("raw", "user_text", "I optimized the Atlas database query."),
            ("clean", "cleaned", "I optimized the Atlas database query."),
            ("sum", "summary", "Optimized Atlas database performance."),
        ):
            _ = db.execute(
                "INSERT INTO artifacts(id,entry_id,kind,content,created_at,revision) "
                "VALUES (?,'entry',?,?,'2026-11-01T09:00:00Z',1)",
                (artifact_id, kind, content),
            )
        for job_id, kind in (("cleanup", "cleanup"), ("summary", "summarize")):
            _ = db.execute(
                "INSERT INTO jobs(id,entry_id,kind,target,status,not_before,created_at,revision) "
                "VALUES (?,'entry',?,?,'done',0,'2026-11-01T09:00:00Z',1)",
                (job_id, kind, kind),
            )
        _ = db.execute("INSERT INTO job_dependencies VALUES ('summary','cleanup')")
    db.close()

    settings = Settings(
        data_dir=tmp_path,
        review={"enabled_for": []},
        embedding={"provider": "fake"},
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    store.migrate()
    entry = store.get_entry("entry")
    assert entry["title"] == "Atlas"
    assert entry["tags"] == ["Work"]
    assert entry["version"] == 1
    assert {item["id"] for item in entry["artifacts"]} == {"raw", "clean", "sum"}
    embeds = [job for job in entry["jobs"] if job["kind"] == "embed"]
    assert len(embeds) == 1
    assert embeds[0]["status"] == "queued"
    with store.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            connection.execute(
                "SELECT 1 FROM job_dependencies WHERE job_id='summary' AND depends_on='cleanup'"
            ).fetchone()
            is not None
        )
    assert Runner(store, settings).run_batch()["failed"] == 0
    assert store.get_entry("entry")["status"] == "done"
    with store.connect() as connection:
        rows = connection.execute("SELECT passage,vector,dimensions FROM embeddings").fetchall()
        assert len(rows) == 2
        assert all(len(row["vector"]) == row["dimensions"] * 4 for row in rows)
    assert json.loads(json.dumps(store.get_entry("entry")))["summary"].startswith("Optimized")


def test_upgrade_reindexes_existing_entries_using_completed_reviews(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    entry = store.create_entry(text="Original source for the journal.")
    cleanup = next(job for job in entry["jobs"] if job["kind"] == "cleanup")
    job = store.claim({cleanup["id"]})
    assert job is not None
    assert store.finish_job(job, "Provisional-only-term", "fake", "digest")
    with store.connect() as db:
        _ = db.execute(
            "DELETE FROM schema_migrations WHERE version='008_final_search_projection.sql'"
        )
        _ = db.execute(
            "UPDATE search_index SET text='Provisional-only-term' WHERE entry_id=?",
            (entry["id"],),
        )
    store.migrate()
    assert store.search_documents()[0]["text"] == "Original source for the journal."
    assert store.fts_search("Provisional-only-term") == []


def test_upgrade_requeues_outputs_after_corrected_cleanup_with_all_reviews_enabled(
    tmp_path: Path,
) -> None:
    db = sqlite3.connect(tmp_path / "journal.db")
    db.execute("PRAGMA foreign_keys=ON")
    _ = db.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
    resources = files("msj").joinpath("resources/migrations")
    for resource in sorted(resources.iterdir(), key=lambda item: item.name):
        if resource.name[:3] > "003" or not resource.name.endswith(".sql"):
            continue
        _ = db.executescript(resource.read_text(encoding="utf-8"))
        _ = db.execute("INSERT INTO schema_migrations VALUES (?)", (resource.name,))
    _ = db.execute(
        "INSERT INTO entries(id,created_at,entry_date,source) "
        "VALUES ('entry','2026-01-01','2026-01-01','text')"
    )
    for artifact_id, kind, content in (
        ("raw", "user_text", "I planned a launch"),
        ("clean", "cleaned", "I launched the product"),
        ("sum", "summary", "Launched the product"),
    ):
        _ = db.execute(
            "INSERT INTO artifacts(id,entry_id,kind,content,created_at,revision) "
            "VALUES (?,'entry',?,?,?,1)",
            (artifact_id, kind, content, "2026-01-01"),
        )
    for job_id, kind, status in (
        ("cleanup", "cleanup", "done"),
        ("summary", "summarize", "done"),
        ("extract", "extract", "done"),
    ):
        _ = db.execute(
            "INSERT INTO jobs(id,entry_id,kind,status,not_before,created_at,revision) "
            "VALUES (?,'entry',?,?,0,'2026-01-01',1)",
            (job_id, kind, status),
        )
    for child in ("summary", "extract"):
        _ = db.execute("INSERT INTO job_dependencies VALUES (?, 'cleanup')", (child,))
    db.commit()
    db.close()

    settings = Settings(
        data_dir=tmp_path,
        embedding={"provider": "none"},
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    entry = store.get_entry("entry")
    assert entry["summary"] is None
    assert {job["kind"]: job["status"] for job in entry["jobs"]}["summarize"] == "queued"
    assert store.fts_search("Launched") == []

    cleanup_review = next(
        job for job in store.list_jobs() if job["kind"] == "review" and job["target"] == "cleanup"
    )
    assert not any(
        job["kind"] == "review" and job["target"] in {"summarize", "extract"}
        for job in store.list_jobs()
    )
    claimed_review = store.claim({cleanup_review["id"]})
    assert claimed_review is not None
    assert store.finish_job(
        claimed_review,
        '{"unsupported_claims":["launched"]}',
        "fake",
        "digest",
        unsupported_claims=("launched",),
    )
    revise = next(
        job for job in store.list_jobs() if job["kind"] == "revise" and job["target"] == "cleanup"
    )
    claimed_revise = store.claim({revise["id"]})
    assert claimed_revise is not None
    assert store.finish_job(claimed_revise, "I planned a launch", "fake", "digest")
    final_review = next(
        job
        for job in store.list_jobs()
        if job["kind"] == "review" and job["target"] == "cleanup" and job["status"] == "queued"
    )
    claimed_final_review = store.claim({final_review["id"]})
    assert claimed_final_review is not None
    assert store.finish_job(
        claimed_final_review,
        '{"unsupported_claims":[]}',
        "fake",
        "digest",
        unsupported_claims=(),
    )

    result = Runner(store, settings).run_batch()
    assert result["failed"] == 0
    updated = store.get_entry("entry")
    assert updated["text"] == "I planned a launch"
    assert updated["summary"] == "I planned a launch"


def test_legacy_token_migration_requires_reissue_and_revokes_sessions(tmp_path: Path) -> None:
    db = sqlite3.connect(tmp_path / "journal.db")
    db.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
    resources = files("msj").joinpath("resources/migrations")
    for resource in sorted(resources.iterdir(), key=lambda item: item.name):
        if resource.name[:3] > "008" or not resource.name.endswith(".sql"):
            continue
        db.executescript(resource.read_text(encoding="utf-8"))
        db.execute("INSERT INTO schema_migrations VALUES (?)", (resource.name,))
    db.execute("INSERT INTO auth VALUES (1, 'scrypt$legacy$digest')")
    db.execute("INSERT INTO sessions VALUES ('old-session','2026-01-01',999999999999)")
    db.commit()
    db.close()
    store = Store(Settings(data_dir=tmp_path, embedding={"provider": "none"}))
    store.migrate()
    assert store.token_hash() == "reissue-required"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
