"""Regressions for job visibility, migration, and storage edge cases."""

import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from msj import storage
from msj.config import Settings
from msj.ownership import OwnerIdentity

AudioCleanupPending = getattr(storage, "AudioCleanupPending", RuntimeError)
NotFound = storage.NotFound
Store = storage.Store


def store_at(path: Path) -> Store:
    store = Store(Settings(data_dir=path))
    store.migrate()
    return store


def test_failed_parent_is_exposed_on_dependent_job_and_entry_then_retry_unblocks(
    tmp_path: Path,
) -> None:
    store = store_at(tmp_path)
    entry = store.create_entry(text="Entry with an inference failure")
    jobs = {job["target"]: job for job in store.list_jobs()}
    cleanup = store.claim({jobs["cleanup"]["id"]})
    assert cleanup is not None
    assert store.fail_job(cleanup, "permanent model failure", retryable=False)

    children = {job["target"]: job for job in store.list_jobs()}
    assert children["summarize"]["blocked_by"][0]["kind"] == "cleanup"
    assert children["summarize"]["blocked_by"][0]["message"] == (
        f"blocked by failed cleanup job {jobs['cleanup']['id']}"
    )
    entry_view = store.get_entry(entry["id"])
    entry_jobs = {job["target"]: job for job in entry_view["jobs"]}
    assert entry_jobs["extract"]["blocked_by"][0]["kind"] == "cleanup"

    _ = store.retry_job(jobs["cleanup"]["id"])
    unblocked = {job["target"]: job for job in store.list_jobs()}
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


def test_remote_audio_reservation_expires_and_logs_reclamation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = store_at(tmp_path)
    reservation = {
        "path": "audio/orphan.wav",
        "owner_host": "unreachable-host",
        "owner_pid": 1,
        "owner_boot_id": "unknown",
        "owner_started_at": "unknown",
        "created_at": time.time() - 3601,
    }

    assert store._write_owner_alive(reservation) is False
    assert "Reclaiming stale audio write reservation" in caplog.text


def test_recent_remote_audio_reservation_remains_protected(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    reservation = {
        "path": "audio/live.wav",
        "owner_host": "remote-host",
        "owner_pid": 1,
        "owner_boot_id": "unknown",
        "owner_started_at": "unknown",
        "created_at": time.time(),
    }

    assert store._write_owner_alive(reservation) is True


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
    with pytest.raises(AudioCleanupPending, match="cleanup remains pending") as caught:
        store.create_entry(audio=b"recording", extension="wav")
    assert caught.value.__cause__ is write_error


def test_audio_creation_failure_is_recovered_from_durable_cleanup_outbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_at(tmp_path)
    original_unlink = Path.unlink

    def fail_audio_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.suffix == ".wav":
            raise PermissionError("secondary cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    def fail_queue(*_args: object) -> None:
        raise RuntimeError("queue failure")

    monkeypatch.setattr(Path, "unlink", fail_audio_unlink)
    monkeypatch.setattr(store, "_queue", fail_queue)
    with pytest.raises(AudioCleanupPending, match="cleanup remains pending") as caught:
        store.create_entry(audio=b"private audio bytes", extension="wav")
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert str(caught.value.__cause__) == "queue failure"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM entries").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM file_deletions").fetchone()[0] == 1
    audio_files = list((tmp_path / "audio").rglob("*.wav"))
    assert len(audio_files) == 1

    monkeypatch.setattr(Path, "unlink", original_unlink)
    Store(store.settings).migrate()
    assert not audio_files[0].exists()
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM file_deletions").fetchone()[0] == 0


def test_audio_write_and_entry_commit_are_exclusive_with_outbox_draining(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_at(tmp_path)
    original_write = Path.write_bytes
    original_drain = store._drain_deletions
    write_started = threading.Event()
    drain_started = threading.Event()
    allow_write = threading.Event()

    def blocked_write(path: Path, data: bytes) -> int:
        write_started.set()
        assert allow_write.wait(5)
        return original_write(path, data)

    def tracked_drain() -> list[tuple[str, int | None]]:
        drain_started.set()
        return original_drain()

    monkeypatch.setattr(Path, "write_bytes", blocked_write)
    monkeypatch.setattr(store, "_drain_deletions", tracked_drain)
    with ThreadPoolExecutor(max_workers=2) as executor:
        creation = executor.submit(store.create_entry, audio=b"recording", extension="wav")
        assert write_started.wait(5)
        drain = executor.submit(store._drain_deletions)
        assert drain_started.wait(5)
        assert not drain.done()
        allow_write.set()
        entry = creation.result(timeout=5)
        _ = drain.result(timeout=5)
    audio_path = next(
        tmp_path / artifact["content"]
        for artifact in entry["artifacts"]
        if artifact["kind"] == "raw_audio"
    )
    assert audio_path.is_file()


def test_startup_reclaims_audio_reservation_from_a_dead_writer(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    relative = Path("audio") / "orphan" / "recording.wav"
    audio_path = tmp_path / relative
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"partial recording")
    with store.connect() as db:
        _ = db.execute("BEGIN IMMEDIATE")
        _ = db.execute("INSERT INTO file_deletions VALUES (?)", (str(relative),))
        _ = db.execute(
            "INSERT INTO file_write_reservations "
            "(path,owner_host,owner_pid,owner_boot_id,owner_started_at,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                str(relative),
                socket.gethostname(),
                2_000_000_000,
                OwnerIdentity.current().boot_id,
                "dead-start",
                time.time(),
            ),
        )

    Store(store.settings).migrate()
    assert not audio_path.exists()
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM file_deletions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM file_write_reservations").fetchone()[0] == 0


def test_entry_detail_reports_compilations_that_deletion_will_remove(tmp_path: Path) -> None:
    from msj.compilations import CompilationService

    store = store_at(tmp_path)
    entry = store.create_entry(text="Source for a compilation")
    _ = CompilationService(store).create([entry["id"]], "Purpose", "Period")
    assert store.get_entry(entry["id"])["compilation_count"] == 1


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
