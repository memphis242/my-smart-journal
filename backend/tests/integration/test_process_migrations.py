"""`msj migrate` builds a fresh schema and upgrades older databases that `msj serve` then serves."""

import sqlite3
from contextlib import closing
from importlib.resources import files
from pathlib import Path

import pytest

from .msj_harness import MsjFactory, MsjInstance

pytestmark = pytest.mark.integration

RAW = "I optimized the Atlas database query."
SUMMARY = "Optimized Atlas database performance."


def _migration_names() -> list[str]:
    resources = files("msj").joinpath("resources/migrations")
    names = sorted(item.name for item in resources.iterdir() if item.name.endswith(".sql"))
    assert names, "no packaged migrations found"
    return names


def _applied(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as db:
        return sorted(row[0] for row in db.execute("SELECT version FROM schema_migrations"))


def _build_legacy_database(database: Path, level: str) -> None:
    """Apply migrations up to `level` and insert one processed text entry, as an older
    release would have left it."""
    resources = files("msj").joinpath("resources/migrations")
    with closing(sqlite3.connect(database)) as db:
        _ = db.execute("PRAGMA foreign_keys=ON")
        _ = db.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
        for name in _migration_names():
            if name[:3] > level:
                continue
            _ = db.executescript(resources.joinpath(name).read_text(encoding="utf-8"))
            _ = db.execute("INSERT INTO schema_migrations VALUES (?)", (name,))
        with db:
            _ = db.execute(
                "INSERT INTO entries(id,created_at,entry_date,title,tags,source) "
                "VALUES ('legacy','2026-11-01T09:00:00Z','2026-11-01','Atlas','[\"Work\"]','text')"
            )
            for artifact_id, kind, content in (
                ("raw", "user_text", RAW),
                ("clean", "cleaned", RAW),
                ("sum", "summary", SUMMARY),
            ):
                _ = db.execute(
                    "INSERT INTO artifacts(id,entry_id,kind,content,created_at,revision) "
                    "VALUES (?,'legacy',?,?,'2026-11-01T09:00:00Z',1)",
                    (artifact_id, kind, content),
                )
            for job_id, kind in (("cleanup", "cleanup"), ("summary", "summarize")):
                _ = db.execute(
                    "INSERT INTO jobs(id,entry_id,kind,target,status,not_before,created_at,"
                    "revision) VALUES (?,'legacy',?,?,'done',0,'2026-11-01T09:00:00Z',1)",
                    (job_id, kind, kind),
                )
            _ = db.execute("INSERT INTO job_dependencies VALUES ('summary','cleanup')")
    assert _applied(database)[-1][:3] == level


def test_migrate_creates_schema_in_empty_data_dir(msj_factory: MsjFactory) -> None:
    msj = msj_factory()
    msj.migrate()
    database = msj.data_dir / "journal.db"
    assert database.is_file()
    assert _applied(database) == _migration_names()
    assert (database.stat().st_mode & 0o077) == 0, "the database must be owner-only"
    msj.migrate()  # idempotent
    assert _applied(database) == _migration_names()

    _ = msj.create_token()
    _ = msj.start_server()
    assert msj.expect(msj.api.get("/api/v1/entries"), 200) == {"items": [], "total": 0}


@pytest.mark.parametrize("level", ["004", "012"])
def test_migrate_upgrades_older_database_and_serves_it(msj_factory: MsjFactory, level: str) -> None:
    msj: MsjInstance = msj_factory()
    database = msj.data_dir / "journal.db"
    _build_legacy_database(database, level)

    msj.migrate()
    assert _applied(database) == _migration_names()
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)

    _ = msj.create_token()
    _ = msj.start_server()
    entry = msj.entry("legacy")
    assert entry["title"] == "Atlas"
    assert entry["tags"] == ["Work"]
    assert entry["entry_date"] == "2026-11-01"
    assert entry["version"] == 1
    assert {"raw", "clean", "sum"} <= {artifact["id"] for artifact in entry["artifacts"]}
    listing = msj.expect(msj.api.get("/api/v1/entries"), 200)
    assert [item["id"] for item in listing["items"]] == ["legacy"]

    # The upgraded entry still processes to completion on the current worker.
    _ = msj.start_worker()
    msj.process_entries(["legacy"])
    processed = msj.entry("legacy")
    assert processed["status"] == "done"
    assert processed["text"] == RAW
    assert processed["summary"]
    patched = msj.expect(
        msj.api.patch("/api/v1/entries/legacy", json={"title": "Atlas v2", "expected_version": 1}),
        200,
    )
    assert patched["version"] == 2
