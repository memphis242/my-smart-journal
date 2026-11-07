"""Regressions for job visibility, migration, and storage edge cases."""

import sqlite3
from pathlib import Path

import pytest

from msj.config import Settings
from msj.storage import NotFound, Store


def store_at(path: Path) -> Store:
    store = Store(Settings(data_dir=path))
    store.migrate()
    return store


def test_failed_parent_is_exposed_on_dependent_job_and_entry_then_retry_unblocks(
    tmp_path: Path,
) -> None:
    store = store_at(tmp_path)
    entry = store.create_entry(text="Entry with an inference failure")
    jobs = {job["kind"]: job for job in store.list_jobs()}
    cleanup = store.claim({jobs["cleanup"]["id"]})
    assert cleanup is not None
    assert store.fail_job(cleanup, "permanent model failure", retryable=False)

    children = {job["kind"]: job for job in store.list_jobs()}
    assert children["review"]["blocked_by"][0]["kind"] == "cleanup"
    assert children["review"]["blocked_by"][0]["message"] == (
        f"blocked by failed cleanup job {jobs['cleanup']['id']}"
    )
    entry_view = store.get_entry(entry["id"])
    entry_jobs = {job["kind"]: job for job in entry_view["jobs"]}
    assert entry_jobs["extract"]["blocked_by"][0]["kind"] == "cleanup"

    _ = store.retry_job(jobs["cleanup"]["id"])
    unblocked = {job["kind"]: job for job in store.list_jobs()}
    assert unblocked["review"]["blocked_by"] == []
    assert unblocked["summarize"]["blocked_by"] == []


def test_delete_and_startup_keep_undeletable_paths_for_later_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_at(tmp_path)
    entry = store.create_entry(audio=b"voice recording", extension="wav")
    audio_path = store.data_dir / next(
        artifact["content"] for artifact in entry["artifacts"] if artifact["kind"] == "raw_audio"
    )
    original_unlink = Path.unlink

    def fail_audio_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path == audio_path:
            raise PermissionError("simulated denied unlink")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_audio_unlink)
    store.delete_entry(entry["id"])
    with pytest.raises(NotFound):
        store.get_entry(entry["id"])
    with store.connect() as db:
        assert db.execute("SELECT path FROM file_deletions").fetchone()[0] == str(
            audio_path.relative_to(store.data_dir)
        )
    reopened = Store(store.settings)
    reopened.migrate()
    with reopened.connect() as db:
        assert db.execute("SELECT count(*) FROM file_deletions").fetchone()[0] == 1
    monkeypatch.setattr(Path, "unlink", original_unlink)
    reopened.migrate()
    assert not audio_path.exists()
    with reopened.connect() as db:
        assert db.execute("SELECT count(*) FROM file_deletions").fetchone()[0] == 0


def test_patch_supersedes_old_revision_jobs_and_status_filter_excludes_them(
    tmp_path: Path,
) -> None:
    store = store_at(tmp_path)
    entry = store.create_entry(text="Original text")
    with store.connect() as db:
        old_dependency_count = db.execute("SELECT count(*) FROM job_dependencies").fetchone()[0]
    _ = store.patch_entry(entry["id"], {"text": "Replacement text"})
    jobs = store.list_jobs()
    old_jobs = [job for job in jobs if job["revision"] == 1]
    assert old_jobs and all(job["status"] == "superseded" for job in old_jobs)
    assert store.list_jobs(status="failed") == []
    assert len(store.list_jobs(status="superseded")) == len(old_jobs)
    assert store.list_jobs(status="queued")
    with store.connect() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        indexes = {row[1] for row in db.execute("PRAGMA index_list(jobs)")}
        assert "jobs_ready" in indexes
        assert db.execute("SELECT count(*) FROM job_dependencies").fetchone()[0] == (
            old_dependency_count * 2
        )
        job_status_sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()[0]
        assert "superseded" in job_status_sql


def test_v2_migration_preserves_dependencies_and_supersedes_old_failed_jobs(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "legacy"
    data_dir.mkdir(mode=0o700)
    db_path = data_dir / "journal.db"
    resources = Path(__file__).parents[1] / "src/msj/resources/migrations"
    connection = sqlite3.connect(db_path)
    try:
        for version in ("001_initial.sql", "002_file_deletions.sql"):
            connection.executescript((resources / version).read_text(encoding="utf-8"))
        _ = connection.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
        _ = connection.executemany(
            "INSERT INTO schema_migrations VALUES (?)",
            [("001_initial.sql",), ("002_file_deletions.sql",)],
        )
        _ = connection.execute(
            "INSERT INTO entries (id,created_at,entry_date,source,revision) "
            "VALUES ('entry','now','2026-01-01','text',2)"
        )
        _ = connection.executemany(
            "INSERT INTO jobs (id,entry_id,kind,status,attempts,not_before,created_at,revision) "
            "VALUES (?,?,?,'failed',1,0,'now',?)",
            [("old", "entry", "cleanup", 1), ("current", "entry", "summarize", 2)],
        )
        _ = connection.execute(
            "INSERT INTO job_dependencies (job_id,depends_on) VALUES ('current','old')"
        )
        connection.commit()
    finally:
        connection.close()

    store = Store(Settings(data_dir=data_dir))
    store.migrate()
    jobs = {job["id"]: job for job in store.list_jobs()}
    assert jobs["old"]["status"] == "superseded"
    assert jobs["current"]["status"] == "failed"
    with store.connect() as db:
        dependencies = [
            tuple(row) for row in db.execute("SELECT job_id,depends_on FROM job_dependencies")
        ]
        assert dependencies == [("current", "old")]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_entry_dates_are_normalized_on_create_and_patch(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    entry = store.create_entry(text="Date normalization", entry_date="20260102")
    assert entry["entry_date"] == "2026-01-02"
    patched = store.patch_entry(entry["id"], {"entry_date": "20260103"})
    assert patched["entry_date"] == "2026-01-03"


def test_audio_creation_suppresses_cleanup_errors_and_surfaces_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_at(tmp_path)
    write_error = OSError("original audio write error")

    def fail_write(_path: Path, _data: bytes) -> int:
        raise write_error

    def fail_cleanup(_path: Path, *, missing_ok: bool = False) -> None:
        raise PermissionError("secondary cleanup error")

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    monkeypatch.setattr(Path, "rmdir", fail_cleanup)
    with pytest.raises(OSError, match="original audio write error") as caught:
        store.create_entry(audio=b"recording", extension="wav")
    assert caught.value is write_error


def test_fail_job_can_be_marked_non_retryable(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    _entry = store.create_entry(text="Do not retry this")
    job = next(job for job in store.list_jobs() if job["kind"] == "cleanup")
    claim = store.claim({job["id"]})
    assert claim is not None and claim["attempts"] == 1
    assert store.fail_job(claim, "audio too large", retryable=False)
    failed = next(job for job in store.list_jobs() if job["id"] == claim["id"])
    assert failed["status"] == "failed"
    assert failed["last_error"] == "audio too large"


def test_delete_continues_after_one_file_error_and_api_returns_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from fastapi.testclient import TestClient

    from msj.api import create_app
    from msj.auth import create_token

    store = store_at(tmp_path)
    first = store.create_entry(audio=b"private-audio-first", extension="wav")
    second = store.create_entry(audio=b"private-audio-second", extension="wav")
    paths = {
        entry["id"]: store.data_dir
        / next(
            artifact["content"]
            for artifact in entry["artifacts"]
            if artifact["kind"] == "raw_audio"
        )
        for entry in (first, second)
    }
    app = create_app(store.settings)
    token = create_token(app.state.store)
    original_unlink = Path.unlink

    def fail_first(path: Path, *, missing_ok: bool = False) -> None:
        if path == paths[first["id"]]:
            raise PermissionError("denied test deletion")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_first)
    # Leave one item pending before the second delete drains both outbox rows.
    store.delete_entry(first["id"])
    with TestClient(app) as client:
        response = client.delete(
            f"/api/v1/entries/{second['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204
    assert paths[first["id"]].exists()
    with pytest.raises(NotFound):
        store.get_entry(first["id"])
    assert not paths[second["id"]].exists()
    assert "PermissionError" in caplog.text
    assert "private-audio-first" not in caplog.text
    # Startup migration sees the same pending outbox row and leaves startup available.
    Store(store.settings).migrate()
