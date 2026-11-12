"""Transactional SQLite journal storage and immutable artifact lineage.

The Store interface is the seam for a future encrypted storage implementation.
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from .config import Settings, TaskRoute
from .ownership import OwnerIdentity

_logger = logging.getLogger(__name__)
_SEARCH_PROJECTION_MIGRATION: Final = "008_final_search_projection.sql"


class NotFound(ValueError):
    """The requested resource does not exist."""


class Conflict(ValueError):
    """The requested transition is not valid for the resource's state."""


class EntryChanged(Conflict):
    def __init__(self, current_version: int) -> None:
        self.current_version = current_version
        super().__init__(
            f"This entry was changed elsewhere (now version {current_version}); "
            "reload it and reapply your changes."
        )


class AudioCleanupPending(RuntimeError):
    """Audio creation failed and removal of its untracked file is still pending."""

    def __init__(self, filename: str, errno: int | None) -> None:
        self.filename = filename
        self.errno = errno
        super().__init__(
            f"Audio creation failed; cleanup remains pending for {filename} (errno {errno})."
        )


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def identifier() -> str:
    return str(uuid4())


class Store:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.data_dir = settings.data_dir.expanduser().resolve()
        self.db_path = self.data_dir / "journal.db"

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            _ = connection.execute("PRAGMA foreign_keys=ON")
            _ = connection.execute("PRAGMA busy_timeout=30000")
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)
        descriptor = os.open(self.db_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.db_path.chmod(0o600)
        with self.connect() as db:
            _ = db.execute("PRAGMA journal_mode=WAL")
            _ = db.execute("BEGIN IMMEDIATE")
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)"
            )
            applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
            resources = files("msj").joinpath("resources/migrations")
            rebuild_search = False
            for resource in sorted(resources.iterdir(), key=lambda path: path.name):
                if resource.name.endswith(".sql") and resource.name not in applied:
                    # Execute complete statements without executescript's implicit commit.
                    statement = ""
                    for line in resource.read_text(encoding="utf-8").splitlines(keepends=True):
                        statement += line
                        if sqlite3.complete_statement(statement):
                            _ = db.execute(statement)
                            statement = ""
                    assert not statement.strip()
                    _ = db.execute("INSERT INTO schema_migrations VALUES (?)", (resource.name,))
                    if resource.name == _SEARCH_PROJECTION_MIGRATION:
                        rebuild_search = True
            self._reconcile_review_sessions(db)
            from .embedding import EmbeddingService

            EmbeddingService(self, self.settings).reconcile(db)
            if rebuild_search:
                entry_ids = [row[0] for row in db.execute("SELECT id FROM entries")]
                for entry_id in entry_ids:
                    self._refresh_search(db, entry_id)
        _ = self._drain_deletions()

    def _reconcile_review_sessions(self, db: sqlite3.Connection) -> None:
        rows = db.execute(
            "SELECT j.* FROM jobs j "
            "JOIN entries e ON e.id=j.entry_id WHERE j.kind IN ('cleanup','summarize','extract') "
            "AND j.revision=e.revision AND j.status!='superseded' ORDER BY j.created_at,j.id"
        ).fetchall()
        for row in rows:
            target = str(row["kind"])
            exists = db.execute(
                "SELECT 1 FROM review_sessions WHERE entry_id=? AND revision=? AND target=?",
                (row["entry_id"], row["revision"], target),
            ).fetchone()
            if exists is not None:
                continue
            source_id = self._find_source_id(db, row, target)
            artifact_kind = {"cleanup": "cleaned", "summarize": "summary", "extract": "extraction"}[
                target
            ]
            candidate = db.execute(
                "SELECT id FROM artifacts WHERE entry_id=? AND revision=? AND kind=? "
                "AND superseded_by IS NULL ORDER BY created_at DESC,id DESC LIMIT 1",
                (row["entry_id"], row["revision"], artifact_kind),
            ).fetchone()
            enabled = target in self.settings.review.enabled_for
            completed = row["status"] == "done" and candidate is not None and source_id is not None
            status = "pending" if enabled else "disabled"
            _ = db.execute(
                "INSERT INTO review_sessions (entry_id,revision,target,enabled,max_turns,status,"
                "source_artifact_id,candidate_artifact_id,final_artifact_id,producer_route) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    row["entry_id"],
                    row["revision"],
                    target,
                    int(enabled),
                    self.settings.review.max_turns,
                    status,
                    source_id,
                    candidate["id"] if candidate is not None else None,
                    candidate["id"] if candidate is not None else None,
                    self.settings.tasks[row["kind"]].model_dump_json() if completed else None,
                ),
            )
            if enabled and completed:
                assert candidate is not None
                review_id = self._insert_session_job(
                    db,
                    dict(row),
                    "review",
                    target,
                    1,
                    input_artifact_id=str(candidate["id"]),
                    depends_on=str(row["id"]),
                )
                if target == "cleanup":
                    self._gate_cleanup_children(db, dict(row), review_id)
        cleanup_session = db.execute(
            "SELECT * FROM review_sessions WHERE revision=(SELECT revision FROM entries "
            "WHERE id=review_sessions.entry_id) AND target='cleanup' AND status='pending'"
        ).fetchall()
        for session in cleanup_session:
            gate = db.execute(
                "SELECT id FROM jobs WHERE entry_id=? AND revision=? AND target='cleanup' "
                "AND kind IN ('review','revise') AND status IN ('queued','running','failed') "
                "ORDER BY turn DESC,created_at DESC LIMIT 1",
                (session["entry_id"], session["revision"]),
            ).fetchone()
            if gate is not None:
                self._gate_cleanup_children(
                    db,
                    {"entry_id": session["entry_id"], "revision": session["revision"]},
                    str(gate["id"]),
                )
        self._invalidate_outputs_behind_cleanup_review(db)

    def _invalidate_outputs_behind_cleanup_review(self, db: sqlite3.Connection) -> None:
        """Requeue published outputs when a newly pending cleanup review can change them."""
        entries = db.execute(
            "SELECT s.entry_id,s.revision FROM review_sessions s "
            "WHERE s.target='cleanup' AND s.status='pending' "
            "AND EXISTS (SELECT 1 FROM jobs j WHERE j.entry_id=s.entry_id "
            "AND j.revision=s.revision AND j.kind='cleanup' AND j.status='done') "
            "AND EXISTS (SELECT 1 FROM jobs j WHERE j.entry_id=s.entry_id "
            "AND j.revision=s.revision AND j.kind IN ('summarize','extract') "
            "AND j.status='done')"
        ).fetchall()
        for entry in entries:
            entry_id, revision = str(entry["entry_id"]), int(entry["revision"])
            cleaned = self._find_source_id(
                db,
                {"entry_id": entry_id, "revision": revision},
                "summarize",
            )
            _ = db.execute(
                "UPDATE jobs SET status='queued',attempts=0,last_error=NULL,finished_at=NULL,"
                "not_before=0,lease_until=NULL,claim_token=NULL,claim_protocol=0 "
                "WHERE entry_id=? AND revision=? AND target IN ('summarize','extract') "
                "AND status IN ('done','failed','superseded')",
                (entry_id, revision),
            )
            _ = db.execute(
                "DELETE FROM jobs WHERE entry_id=? AND revision=? "
                "AND kind IN ('review','revise') AND target IN ('summarize','extract')",
                (entry_id, revision),
            )
            gate = db.execute(
                "SELECT id FROM jobs WHERE entry_id=? AND revision=? AND target='cleanup' "
                "AND kind IN ('review','revise') AND status IN ('queued','running','failed') "
                "ORDER BY turn DESC,created_at DESC LIMIT 1",
                (entry_id, revision),
            ).fetchone()
            if gate is not None:
                self._gate_cleanup_children(
                    db,
                    {"entry_id": entry_id, "revision": revision},
                    str(gate["id"]),
                )
            _ = db.execute(
                "UPDATE review_sessions SET status='pending',source_artifact_id=?,"
                "candidate_artifact_id=NULL,final_artifact_id=NULL,turns_used=0,"
                "remaining_claims='[]',producer_route=NULL WHERE entry_id=? AND revision=? "
                "AND target IN ('summarize','extract')",
                (cleaned, entry_id, revision),
            )
            _ = db.execute(
                "DELETE FROM embeddings WHERE entry_id=? AND revision=?", (entry_id, revision)
            )
            _ = db.execute(
                "UPDATE jobs SET status='queued',attempts=0,last_error=NULL,finished_at=NULL,"
                "not_before=0,lease_until=NULL,claim_token=NULL,claim_protocol=0 "
                "WHERE entry_id=? AND revision=? AND kind='embed' "
                "AND status IN ('done','failed','superseded')",
                (entry_id, revision),
            )
            self._refresh_search(db, entry_id)

    @staticmethod
    def _find_source_id(db: sqlite3.Connection, job: Mapping[str, Any], target: str) -> str | None:
        kinds = ("raw_transcript", "user_text") if target == "cleanup" else ("cleaned",)
        for kind in kinds:
            row = db.execute(
                "SELECT id FROM artifacts WHERE entry_id=? AND revision=? AND kind=? "
                "AND superseded_by IS NULL ORDER BY created_at DESC,id DESC LIMIT 1",
                (job["entry_id"], job["revision"], kind),
            ).fetchone()
            if row is not None:
                return str(row["id"])
        return None

    @staticmethod
    def _entry(db: sqlite3.Connection, entry_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise NotFound("Entry not found")
        result = dict(row)
        result["tags"] = json.loads(result["tags"])
        return result

    @staticmethod
    def _artifact(
        db: sqlite3.Connection,
        entry_id: str,
        revision: int,
        kind: str,
        content: str,
        *,
        model: str | None = None,
        prompt_sha256: str | None = None,
        target: str | None = None,
        turn: int = 0,
        reviewed_artifact_id: str | None = None,
        content_type: str | None = None,
    ) -> str:
        artifact_id = identifier()
        previous = db.execute(
            "SELECT id FROM artifacts WHERE entry_id=? AND kind=? AND superseded_by IS NULL "
            "AND COALESCE(target,'')=COALESCE(?,'')",
            (entry_id, kind, target),
        ).fetchall()
        _ = db.execute(
            "INSERT INTO artifacts "
            "(id,entry_id,kind,content,model,prompt_sha256,created_at,revision,target,turn,"
            "reviewed_artifact_id,content_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                artifact_id,
                entry_id,
                kind,
                content,
                model,
                prompt_sha256,
                timestamp(),
                revision,
                target,
                turn,
                reviewed_artifact_id,
                content_type,
            ),
        )
        for row in previous:
            _ = db.execute(
                "UPDATE artifacts SET superseded_by=? WHERE id=?", (artifact_id, row["id"])
            )
        return artifact_id

    def _queue(self, db: sqlite3.Connection, entry_id: str, revision: int, audio: bool) -> None:
        kinds = (["transcribe"] if audio else []) + ["cleanup", "summarize", "extract"]
        ids = {
            (kind, kind if kind in {"cleanup", "summarize", "extract"} else ""): identifier()
            for kind in kinds
        }
        for (kind, target), job_id in ids.items():
            _ = db.execute(
                "INSERT INTO jobs (id,entry_id,kind,target,turn,not_before,created_at,revision) "
                "VALUES (?,?,?,?,0,?,?,?)",
                (job_id, entry_id, kind, target, time.time(), timestamp(), revision),
            )
        for child in ("summarize", "extract"):
            _ = db.execute(
                "INSERT INTO job_dependencies VALUES (?,?)",
                (ids[(child, child)], ids[("cleanup", "cleanup")]),
            )
        if audio:
            _ = db.execute(
                "INSERT INTO job_dependencies VALUES (?,?)",
                (ids[("cleanup", "cleanup")], ids[("transcribe", "")]),
            )
        for target in ("cleanup", "summarize", "extract"):
            enabled = target in self.settings.review.enabled_for
            _ = db.execute(
                "INSERT INTO review_sessions (entry_id,revision,target,enabled,max_turns,status) "
                "VALUES (?,?,?,?,?,?)",
                (
                    entry_id,
                    revision,
                    target,
                    int(enabled),
                    self.settings.review.max_turns,
                    "pending" if enabled else "disabled",
                ),
            )
        from .embedding import EmbeddingService

        EmbeddingService(self, self.settings).reconcile(db, entry_ids={entry_id})

    @staticmethod
    def _canonical_date(value: str) -> str:
        return date.fromisoformat(value).isoformat()

    def create_entry(  # noqa: C901, PLR0915
        self,
        *,
        text: str | None = None,
        audio: bytes | None = None,
        extension: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        entry_date: str | None = None,
        audio_content_type: str | None = None,
    ) -> dict[str, Any]:
        if (text is None) == (audio is None):
            raise ValueError("Provide exactly one of text or audio")
        if text is not None and not text.strip():
            raise ValueError("Text must contain non-whitespace characters")
        if audio is not None and not audio:
            raise ValueError("Audio must not be empty")
        if audio is not None and extension not in {
            "wav",
            "mp3",
            "m4a",
            "ogg",
            "webm",
            "flac",
            "mp4",
        }:
            raise ValueError("Unsupported audio extension")
        entry_id = identifier()
        stored_file: Path | None = None
        content = text
        audio_relative: Path | None = None
        try:
            if audio is not None:
                digest = hashlib.sha256(audio).hexdigest()
                relative = Path("audio") / entry_id / f"{digest}.{extension}"
                audio_relative = relative
                stored_file = self.data_dir / relative
                self._reserve_audio_write(relative)
                content = str(relative)
            assert content is not None
            with self.connect() as db:
                _ = db.execute("BEGIN IMMEDIATE")
                if audio_relative is not None:
                    # Keep the cleanup obligation present through the file and entry commit.
                    _ = db.execute(
                        "INSERT OR IGNORE INTO file_deletions VALUES (?)", (str(audio_relative),)
                    )
                    assert audio is not None
                    assert stored_file is not None
                    (self.data_dir / "audio").mkdir(exist_ok=True, mode=0o700)
                    stored_file.parent.mkdir(mode=0o700)
                    _ = stored_file.write_bytes(audio)
                    stored_file.chmod(0o600)
                _ = db.execute(
                    "INSERT INTO entries (id,created_at,entry_date,title,tags,source) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        entry_id,
                        timestamp(),
                        self._canonical_date(entry_date)
                        if entry_date is not None
                        # Clients send their own calendar day; this fallback is the server's.
                        else date.today().isoformat(),  # noqa: DTZ011
                        title,
                        json.dumps(tags or []),
                        "audio" if audio is not None else "text",
                    ),
                )
                _ = self._artifact(
                    db,
                    entry_id,
                    1,
                    "raw_audio" if audio is not None else "user_text",
                    content,
                    content_type=audio_content_type if audio is not None else None,
                )
                self._queue(db, entry_id, 1, audio is not None)
                self._refresh_search(db, entry_id)
                if audio_relative is not None:
                    _ = db.execute(
                        "DELETE FROM file_deletions WHERE path=?", (str(audio_relative),)
                    )
                    _ = db.execute(
                        "DELETE FROM file_write_reservations WHERE path=?",
                        (str(audio_relative),),
                    )
                # Read the response inside the transaction: once it commits, nothing
                # can fail, so an error always means the entry was not saved.
                created = self.read_entry(db, entry_id, history=False)
        except Exception as original_error:
            if audio_relative is not None:
                try:
                    self._reserve_file_deletion(audio_relative)
                    self._release_audio_write(audio_relative)
                    failures = self._drain_deletions()
                except Exception as cleanup_error:  # noqa: BLE001 - reported as AudioCleanupPending
                    _logger.error(
                        "Audio cleanup retry failed (errno=%s)",
                        getattr(cleanup_error, "errno", None),
                    )
                    raise AudioCleanupPending(
                        audio_relative.name, getattr(cleanup_error, "errno", None)
                    ) from original_error
                pending = next((item for item in failures if item[0] == str(audio_relative)), None)
                if pending is not None:
                    raise AudioCleanupPending(audio_relative.name, pending[1]) from original_error
            raise
        assert created["id"] == entry_id
        return created

    @staticmethod
    def read_entry(db: sqlite3.Connection, entry_id: str, history: bool) -> dict[str, Any]:
        entry = Store._entry(db, entry_id)
        entry["compilation_count"] = int(
            db.execute(
                "SELECT COUNT(*) FROM compilations c JOIN json_each(c.entry_ids) ids "
                "ON ids.value=?",
                (entry_id,),
            ).fetchone()[0]
        )
        rows = db.execute(
            "SELECT * FROM artifacts WHERE entry_id=? "  # noqa: S608 - fixed SQL fragments
            + ("" if history else "AND revision=? AND superseded_by IS NULL ")
            + "ORDER BY created_at,id",
            (entry_id,) if history else (entry_id, entry["revision"]),
        ).fetchall()
        entry["artifacts"] = [
            dict(row) | {"needs_attention": bool(row["needs_attention"])} for row in rows
        ]
        entry["needs_attention"] = any(
            row["needs_attention"]
            for row in rows
            if row["revision"] == entry["revision"] and row["superseded_by"] is None
        )
        job_rows = db.execute(
            "SELECT * FROM jobs WHERE entry_id=? "  # noqa: S608 - fixed SQL fragments
            + ("" if history else "AND revision=? ")
            + "ORDER BY created_at,id",
            (entry_id,) if history else (entry_id, entry["revision"]),
        ).fetchall()
        entry["jobs"] = Store._job_views(db, job_rows)
        sessions = db.execute(
            "SELECT * FROM review_sessions WHERE entry_id=? AND revision=? ORDER BY target",
            (entry_id, entry["revision"]),
        ).fetchall()
        session_views: dict[str, Any] = {}
        for session in sessions:
            final_id = session["final_artifact_id"] or session["candidate_artifact_id"]
            final_row = (
                db.execute("SELECT * FROM artifacts WHERE id=?", (final_id,)).fetchone()
                if final_id is not None
                else None
            )
            review_rows = db.execute(
                "SELECT * FROM artifacts WHERE entry_id=? AND revision=? AND kind='review' "
                "AND target=? AND turn>0 ORDER BY turn,created_at,id",
                (entry_id, entry["revision"], session["target"]),
            ).fetchall()
            session_views[session["target"]] = {
                "final_artifact": (
                    dict(final_row) | {"needs_attention": bool(final_row["needs_attention"])}
                    if final_row is not None
                    else None
                ),
                "turns_used": session["turns_used"],
                "status": session["status"],
                "remaining_claims": json.loads(session["remaining_claims"]),
                "history": [
                    dict(row) | {"needs_attention": bool(row["needs_attention"])}
                    for row in review_rows
                ],
            }
        entry["review_sessions"] = session_views
        from .entry_views import summary_fields

        entry.update(summary_fields(entry))
        return entry

    def get_entry(self, entry_id: str, history: bool = False) -> dict[str, Any]:
        with self.connect() as db:
            _ = db.execute("BEGIN")
            return self.read_entry(db, entry_id, history)

    def list_entries(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connect() as db:
            _ = db.execute("BEGIN")
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM entries ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]
            return [self.read_entry(db, entry_id, False) for entry_id in ids]

    def all_entries(self, *, db: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        """Read the complete current corpus from one consistent SQLite snapshot."""
        if db is not None:
            ids = [row[0] for row in db.execute("SELECT id FROM entries ORDER BY created_at,id")]
            return [self.read_entry(db, entry_id, False) for entry_id in ids]
        with self.connect() as connection:
            _ = connection.execute("BEGIN")
            return self.all_entries(db=connection)

    def patch_entry(
        self,
        entry_id: str,
        changes: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        allowed = {"title", "tags", "entry_date", "text"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("Patch must contain title, tags, entry_date or text")
        text = changes.get("text")
        if "text" in changes and (not isinstance(text, str) or not text.strip()):
            raise ValueError("Text must contain non-whitespace characters")
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            entry = self._entry(db, entry_id)
            if expected_version is not None and expected_version != int(entry["version"]):
                raise EntryChanged(int(entry["version"]))
            for field in ("title", "tags", "entry_date"):
                if field in changes:
                    value = (
                        json.dumps(changes[field])
                        if field == "tags"
                        else (
                            self._canonical_date(changes[field])
                            if field == "entry_date"
                            else changes[field]
                        )
                    )
                    # `field` iterates a fixed tuple of column names.
                    _ = db.execute(f"UPDATE entries SET {field}=? WHERE id=?", (value, entry_id))  # noqa: S608
            if text is not None:
                revision = int(entry["revision"]) + 1
                _ = db.execute("UPDATE entries SET revision=? WHERE id=?", (revision, entry_id))
                # Existing in-flight claims cannot publish into a newer revision.
                _ = db.execute(
                    "UPDATE jobs SET status='superseded',"
                    "last_error='Entry text revised; use current jobs',"
                    "finished_at=?,claim_token=NULL,lease_until=NULL,claim_protocol=0 "
                    "WHERE entry_id=? "
                    "AND status IN ('queued','running','failed')",
                    (timestamp(), entry_id),
                )
                _ = self._artifact(db, entry_id, revision, "user_text", text)
                self._queue(db, entry_id, revision, False)
            version = int(entry["version"]) + 1
            _ = db.execute("UPDATE entries SET version=? WHERE id=?", (version, entry_id))
            self._refresh_search(db, entry_id)
        return self.get_entry(entry_id)

    def delete_entry(self, entry_id: str) -> None:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            _ = self._entry(db, entry_id)
            paths = [
                row[0]
                for row in db.execute(
                    "SELECT content FROM artifacts WHERE entry_id=? AND kind='raw_audio'",
                    (entry_id,),
                )
            ]
            for path in paths:
                _ = db.execute("INSERT OR IGNORE INTO file_deletions VALUES (?)", (path,))
            _ = db.execute("DELETE FROM search_index WHERE entry_id=?", (entry_id,))
            _ = db.execute(
                "DELETE FROM compilations WHERE EXISTS "
                "(SELECT 1 FROM json_each(compilations.entry_ids) WHERE value=?)",
                (entry_id,),
            )
            _ = db.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        _ = self._drain_deletions()

    def _reserve_file_deletion(self, relative: Path) -> None:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            _ = db.execute("INSERT OR IGNORE INTO file_deletions VALUES (?)", (str(relative),))

    def _reserve_audio_write(self, relative: Path) -> None:
        owner = OwnerIdentity.current()
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            _ = db.execute("INSERT OR IGNORE INTO file_deletions VALUES (?)", (str(relative),))
            _ = db.execute(
                "INSERT INTO file_write_reservations "
                "(path,owner_host,owner_pid,owner_boot_id,owner_started_at,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    str(relative),
                    owner.host,
                    owner.pid,
                    owner.boot_id,
                    owner.started_at,
                    time.time(),
                ),
            )

    def _release_audio_write(self, relative: Path) -> None:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            _ = db.execute("DELETE FROM file_write_reservations WHERE path=?", (str(relative),))

    def _write_owner_alive(self, row: sqlite3.Row) -> bool:
        if row["owner_host"] != OwnerIdentity.current().host:
            max_age_s = max(3600.0, self.settings.lease_s * 2)
            if time.time() - float(row["created_at"]) > max_age_s:
                _logger.warning(
                    "Reclaiming stale audio write reservation for managed file %s from host %s",
                    Path(row["path"]).name,
                    row["owner_host"],
                )
                return False
            return True
        return OwnerIdentity.alive(row)

    def _drain_deletions(self) -> list[tuple[str, int | None]]:
        # Durable outbox: deletion failures can be retried at the next startup.
        failures: list[tuple[str, int | None]] = []
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            paths = [row[0] for row in db.execute("SELECT path FROM file_deletions")]
            for relative in paths:
                reservation = db.execute(
                    "SELECT * FROM file_write_reservations WHERE path=?", (relative,)
                ).fetchone()
                if reservation is not None and self._write_owner_alive(reservation):
                    continue
                if reservation is not None:
                    _ = db.execute("DELETE FROM file_write_reservations WHERE path=?", (relative,))
                referenced = db.execute(
                    "SELECT 1 FROM artifacts WHERE kind='raw_audio' AND content=? LIMIT 1",
                    (relative,),
                ).fetchone()
                if referenced is not None:
                    _ = db.execute("DELETE FROM file_deletions WHERE path=?", (relative,))
                    continue
                path = self.data_dir / relative
                try:
                    path.unlink(missing_ok=True)
                    if path.parent.exists() and not any(path.parent.iterdir()):
                        path.parent.rmdir()
                except OSError as exc:
                    _logger.warning(
                        "Audio cleanup failed for managed file %s (errno=%s, cause=%s); "
                        "will retry later",
                        path.name,
                        exc.errno,
                        type(exc).__name__,
                    )
                    failures.append((str(relative), exc.errno))
                else:
                    _ = db.execute("DELETE FROM file_deletions WHERE path=?", (relative,))
        return failures

    def list_jobs(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in {
            "queued",
            "running",
            "done",
            "failed",
            "superseded",
        }:
            raise ValueError("Unknown job status")
        with self.connect() as db:
            _ = db.execute("BEGIN")
            rows = db.execute(
                "SELECT * FROM jobs"  # noqa: S608 - fixed SQL fragments
                + (" WHERE status=?" if status is not None else "")
                + " ORDER BY created_at,id",
                () if status is None else (status,),
            ).fetchall()
            return self._job_views(db, rows)

    @staticmethod
    def _job_views(db: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        if not rows:
            return []
        blocked: dict[str, list[dict[str, str]]] = {}
        for parent in db.execute(
            "WITH RECURSIVE ancestors(job_id,ancestor_id) AS ("
            "SELECT job_id,depends_on FROM job_dependencies "
            "WHERE job_id IN (SELECT value FROM json_each(?)) UNION "
            "SELECT a.job_id,d.depends_on FROM ancestors a "
            "JOIN job_dependencies d ON d.job_id=a.ancestor_id) "
            "SELECT a.job_id,p.id,p.kind FROM ancestors a JOIN jobs p ON p.id=a.ancestor_id "
            "WHERE p.status='failed' ORDER BY a.job_id,p.id",
            (json.dumps([row["id"] for row in rows]),),
        ):
            blocked.setdefault(parent["job_id"], []).append(
                {
                    "job_id": parent["id"],
                    "kind": parent["kind"],
                    "message": f"blocked by failed {parent['kind']} job {parent['id']}",
                }
            )
        return [dict(row) | {"blocked_by": blocked.get(row["id"], [])} for row in rows]

    def retry_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise NotFound("Job not found")
            entry = self._entry(db, job["entry_id"]) if job["entry_id"] is not None else None
            if job["status"] != "failed" or (
                entry is not None and job["revision"] != entry["revision"]
            ):
                raise Conflict("Only failed jobs for the current entry revision can be retried")
            _ = db.execute(
                "UPDATE jobs SET status='queued',attempts=0,last_error=NULL,"
                "finished_at=NULL,not_before=?,claim_protocol=0 WHERE id=?",
                (time.time(), job_id),
            )
            if job["kind"] == "compile":
                _ = db.execute(
                    "UPDATE compilations SET status='queued',last_error=NULL WHERE id=?",
                    (job["compilation_id"],),
                )
            result = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            assert result is not None
            return self._job_views(db, [result])[0]

    @staticmethod
    def batch_owned(db: sqlite3.Connection, job: Mapping[str, Any]) -> bool:
        batch_id = job.get("_batch_id")
        if batch_id is None:
            return True
        return (
            db.execute(
                "SELECT 1 FROM batch_state WHERE id=1 AND batch_id=? AND lease_until>?",
                (batch_id, time.time()),
            ).fetchone()
            is not None
        )

    def claim(
        self, eligible_ids: set[str], *, batch_id: str | None = None
    ) -> dict[str, Any] | None:
        if not eligible_ids:
            return None
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            if not self.batch_owned(db, {"_batch_id": batch_id}):
                raise Conflict("Batch lease was lost; retry the batch after checking worker health")
            now = time.time()
            # A crashed worker's lease is recovered by the next batch.
            _ = db.execute(
                "UPDATE jobs SET status=CASE WHEN attempts>=? THEN 'failed' ELSE 'queued' END,"
                "last_error=CASE WHEN batch_id IS NOT NULL THEN 'Reclaimed after batch owner ' "
                "|| batch_id || ' stopped; if this repeats, the job may be crashing the worker - "
                "check worker memory limits and logs' ELSE 'Worker lease expired; retry after "
                "checking worker health' END,"
                "finished_at=CASE WHEN attempts>=? THEN ? ELSE NULL END,claim_token=NULL,"
                "lease_until=NULL,claim_protocol=0 WHERE status='running' AND lease_until<=?",
                (self.settings.max_attempts, self.settings.max_attempts, timestamp(), now),
            )
            if batch_id is not None:
                _ = db.execute(
                    "UPDATE jobs SET status='queued',last_error='Reclaimed after batch owner ' "
                    "|| COALESCE(batch_id,'unknown') || ' stopped; if this repeats, the job may "
                    "be crashing the worker - check worker memory limits and logs',"
                    "finished_at=NULL,"
                    "not_before=?,lease_until=NULL,claim_token=NULL,claim_protocol=0 "
                    "WHERE status='running' AND (batch_id IS NULL OR batch_id!=?)",
                    (now, batch_id),
                )
            _ = db.execute(
                "UPDATE jobs SET status='failed',"
                "last_error=CASE WHEN last_error LIKE 'Reclaimed after batch owner %' "
                "THEN last_error || '; attempt limit reached; retry after checking worker health' "
                "ELSE 'Attempt limit reached; retry after checking worker health' END,"
                "finished_at=?,claim_token=NULL,lease_until=NULL,claim_protocol=0 "
                "WHERE status='queued' AND attempts>=?",
                (timestamp(), self.settings.max_attempts),
            )
            from .compilations import CompilationService

            CompilationService.reconcile(db)
            rows = db.execute(
                "SELECT j.* FROM jobs j LEFT JOIN entries e ON e.id=j.entry_id "
                "WHERE j.status='queued' AND j.not_before<=? "
                "AND (j.kind='compile' OR j.revision=e.revision) "
                "AND NOT EXISTS (SELECT 1 FROM job_dependencies d JOIN jobs p ON p.id=d.depends_on "
                "WHERE d.job_id=j.id AND p.status!='done') "
                "AND (j.kind!='embed' OR NOT EXISTS (SELECT 1 FROM review_sessions s "
                "WHERE s.entry_id=j.entry_id AND s.revision=j.revision "
                "AND s.target IN ('cleanup','summarize') AND s.status='pending')) "
                "ORDER BY j.created_at,j.id",
                (now,),
            ).fetchall()
            _ = db.execute(
                "UPDATE compilations SET status=(SELECT status FROM jobs "
                "WHERE compilation_id=compilations.id),last_error=(SELECT last_error FROM jobs "
                "WHERE compilation_id=compilations.id) WHERE status IN ('queued','running')"
            )
            candidates = [dict(row) for row in rows if row["id"] in eligible_ids]
            if not candidates:
                return None

            def group(job: dict[str, Any]) -> tuple[int, str, str, str, str]:
                if job["kind"] == "embed":
                    return (20, "local", "", "", str(job["id"]))
                route = self._job_route(db, job)
                target = job["target"]
                turn = int(job["turn"])
                stage = {
                    "transcribe": 0,
                    "cleanup": 1,
                    "review": 2 * turn if target == "cleanup" else 8 + 2 * turn - 1,
                    "revise": 2 * turn + 1 if target == "cleanup" else 8 + 2 * turn,
                    "summarize": 8,
                    "extract": 8,
                    "compile": 21,
                }[job["kind"]]
                return (
                    stage,
                    route.provider,
                    route.endpoint_id or route.base_url,
                    route.model,
                    job["id"],
                )

            job = min(candidates, key=group)
            claim_token = identifier()
            if job["kind"] == "compile":
                _ = CompilationService.job_inputs(db, job["compilation_id"])
            _ = db.execute(
                "UPDATE jobs SET status='running',attempts=attempts+1,lease_until=?,"
                "claim_token=?,claim_protocol=4,batch_id=? WHERE id=?",
                (now + self.settings.lease_s, claim_token, batch_id, job["id"]),
            )
            if job["kind"] == "compile":
                _ = db.execute(
                    "UPDATE compilations SET status='running' WHERE id=?", (job["compilation_id"],)
                )
            return job | {
                "status": "running",
                "attempts": job["attempts"] + 1,
                "claim_token": claim_token,
                "claim_protocol": 4,
                "_batch_id": batch_id,
            }

    def _job_route(self, db: sqlite3.Connection, job: dict[str, Any]) -> TaskRoute:
        if job["kind"] == "revise":
            row = db.execute(
                "SELECT producer_route FROM review_sessions WHERE entry_id=? "
                "AND revision=? AND target=?",
                (job["entry_id"], job["revision"], job["target"]),
            ).fetchone()
            if row is None or row["producer_route"] is None:
                raise Conflict("Original producer route is unavailable; start a new entry revision")
            return TaskRoute.model_validate_json(row["producer_route"])
        return self.settings.tasks[job["kind"]]

    def job_route(self, job: dict[str, Any]) -> TaskRoute:
        with self.connect() as db:
            return self._job_route(db, job)

    def job_inputs(self, job: dict[str, Any]) -> tuple[str, str | None, bytes | None]:
        with self.connect() as db:
            if job["kind"] == "compile":
                from .compilations import CompilationService

                return CompilationService.job_inputs(db, job["compilation_id"]), None, None
            if job["kind"] in {"review", "revise"}:
                session = db.execute(
                    "SELECT * FROM review_sessions WHERE entry_id=? AND revision=? AND target=?",
                    (job["entry_id"], job["revision"], job["target"]),
                ).fetchone()
                assert session is not None
                source_row = db.execute(
                    "SELECT content FROM artifacts WHERE id=?", (session["source_artifact_id"],)
                ).fetchone()
                candidate_row = db.execute(
                    "SELECT content FROM artifacts WHERE id=?", (job["input_artifact_id"],)
                ).fetchone()
                if source_row is None or candidate_row is None:
                    raise Conflict("Review input artifacts are unavailable for this revision")
                return str(source_row["content"]), str(candidate_row["content"]), None
            rows = db.execute(
                "SELECT kind,content FROM artifacts WHERE entry_id=? AND revision=? "
                "AND superseded_by IS NULL",
                (job["entry_id"], job["revision"]),
            )
            artifacts = {row["kind"]: row["content"] for row in rows}
        raw = artifacts.get("user_text", artifacts.get("raw_transcript", ""))
        match job["kind"]:
            case "transcribe":
                path = artifacts["raw_audio"]
                return path, None, (self.data_dir / path).read_bytes()
            case "cleanup":
                return raw, None, None
            case _:
                return artifacts["cleaned"], None, None

    def revision_claims(self, job: dict[str, Any]) -> tuple[str, ...]:
        artifact_id = job.get("review_artifact_id")
        if not isinstance(artifact_id, str):
            raise Conflict("Revision job has no pinned review result")
        with self.connect() as db:
            row = db.execute("SELECT content FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise Conflict("Revision review result is unavailable")
        try:
            value = json.loads(row["content"])
        except ValueError, RecursionError:
            raise Conflict("Pinned review result is invalid") from None
        if not isinstance(value, dict):
            raise Conflict("Pinned review result is invalid")
        claims = value.get("unsupported_claims")
        if not isinstance(claims, list) or not all(isinstance(item, str) for item in claims):
            raise Conflict("Pinned review result is invalid")
        return tuple(claims)

    def finish_job(  # noqa: C901, PLR0912, PLR0915
        self,
        job: dict[str, Any],
        content: str,
        model: str | None,
        prompt_sha256: str | None,
        needs_attention: bool = False,
        *,
        unsupported_claims: tuple[str, ...] | None = None,
        producer_route: TaskRoute | None = None,
    ) -> bool:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            if not self.batch_owned(db, job):
                return False
            if job["kind"] == "compile":
                current_compile = db.execute(
                    "SELECT 1 FROM jobs WHERE id=? AND status='running' AND claim_token=?",
                    (job["id"], job["claim_token"]),
                ).fetchone()
                if current_compile is None:
                    return False
                from .compilations import CompilationService

                assert prompt_sha256 is not None
                CompilationService.finish_job(
                    db, job["compilation_id"], content, model, prompt_sha256
                )
                _ = db.execute(
                    "UPDATE jobs SET status='done',finished_at=?,last_error=NULL,"
                    "lease_until=NULL,claim_token=NULL,claim_protocol=0 WHERE id=?",
                    (timestamp(), job["id"]),
                )
                return True
            current = db.execute(
                "SELECT j.* FROM jobs j JOIN entries e ON e.id=j.entry_id "
                "WHERE j.id=? AND j.status='running' AND j.claim_token=? "
                "AND j.revision=e.revision",
                (job["id"], job["claim_token"]),
            ).fetchone()
            if current is None:
                return False
            kind = {
                "transcribe": "raw_transcript",
                "cleanup": "cleaned",
                "summarize": "summary",
                "extract": "extraction",
                "review": "review",
            }.get(job["kind"])
            if job["kind"] == "revise":
                kind = {"cleanup": "cleaned", "summarize": "summary", "extract": "extraction"}.get(
                    job["target"]
                )
            if kind is None:
                raise Conflict("Unknown processing job kind")
            session = None
            if job["kind"] in {"review", "revise"}:
                session = db.execute(
                    "SELECT * FROM review_sessions WHERE entry_id=? AND revision=? AND target=?",
                    (job["entry_id"], job["revision"], job["target"]),
                ).fetchone()
                assert session is not None
            reviewed_id = job.get("input_artifact_id") if job["kind"] == "review" else None
            artifact_id = self._artifact(
                db,
                job["entry_id"],
                job["revision"],
                kind,
                content,
                model=model,
                prompt_sha256=prompt_sha256,
                target=(
                    job.get("target")
                    if job["kind"] in {"review", "revise"}
                    else (
                        job["kind"] if job["kind"] in {"cleanup", "summarize", "extract"} else None
                    )
                ),
                turn=job.get("turn", 0),
                reviewed_artifact_id=reviewed_id,
            )
            if job["kind"] in {"cleanup", "summarize", "extract"}:
                target = job["target"]
                route = producer_route or self.settings.tasks[target]
                source_id = self._producer_source_id(db, job, target)
                session = db.execute(
                    "SELECT * FROM review_sessions WHERE entry_id=? AND revision=? AND target=?",
                    (job["entry_id"], job["revision"], target),
                ).fetchone()
                assert session is not None
                enabled = bool(session["enabled"])
                _ = db.execute(
                    "UPDATE review_sessions SET source_artifact_id=?,candidate_artifact_id=?,"
                    "final_artifact_id=?,status=?,turns_used=0,producer_route=? "
                    "WHERE entry_id=? AND revision=? "
                    "AND target=?",
                    (
                        source_id,
                        artifact_id,
                        artifact_id,
                        "pending" if enabled else "disabled",
                        route.model_dump_json(),
                        job["entry_id"],
                        job["revision"],
                        target,
                    ),
                )
                if enabled:
                    review_id = self._insert_session_job(
                        db,
                        job,
                        "review",
                        target,
                        1,
                        input_artifact_id=artifact_id,
                        depends_on=job["id"],
                    )
                    if target == "cleanup":
                        self._gate_cleanup_children(db, job, review_id)
                self._set_result_id(db, job["id"], artifact_id)
            elif job["kind"] == "review":
                if unsupported_claims is None:
                    raise Conflict("Review job requires validated unsupported claims")
                claims = list(unsupported_claims)
                target = job["target"]
                turn = int(job["turn"])
                _ = db.execute(
                    "UPDATE review_sessions SET turns_used=?,remaining_claims=?,"
                    "candidate_artifact_id=?,final_artifact_id=? WHERE entry_id=? AND revision=? "
                    "AND target=?",
                    (
                        turn,
                        json.dumps(claims),
                        job["input_artifact_id"],
                        job["input_artifact_id"],
                        job["entry_id"],
                        job["revision"],
                        target,
                    ),
                )
                _ = db.execute(
                    "UPDATE jobs SET result_artifact_id=? WHERE id=?", (artifact_id, job["id"])
                )
                assert session is not None
                max_turns = int(session["max_turns"])
                if not claims or turn >= max_turns:
                    status = "accepted" if not claims else "needs_attention"
                    _ = db.execute(
                        "UPDATE review_sessions SET status=? WHERE entry_id=? AND revision=? "
                        "AND target=?",
                        (status, job["entry_id"], job["revision"], target),
                    )
                    _ = db.execute(
                        "UPDATE artifacts SET needs_attention=? WHERE id=?",
                        (int(bool(claims)), job["input_artifact_id"]),
                    )
                else:
                    revise_id = self._insert_session_job(
                        db,
                        job,
                        "revise",
                        target,
                        turn,
                        input_artifact_id=job["input_artifact_id"],
                        review_artifact_id=artifact_id,
                        depends_on=job["id"],
                    )
                    if target == "cleanup":
                        self._gate_cleanup_children(db, job, revise_id)
            elif job["kind"] == "revise":
                target = job["target"]
                _ = db.execute(
                    "UPDATE review_sessions SET candidate_artifact_id=?,final_artifact_id=? "
                    "WHERE entry_id=? AND revision=? AND target=?",
                    (artifact_id, artifact_id, job["entry_id"], job["revision"], target),
                )
                next_turn = int(job["turn"]) + 1
                review_id = self._insert_session_job(
                    db,
                    job,
                    "review",
                    target,
                    next_turn,
                    input_artifact_id=artifact_id,
                    depends_on=job["id"],
                )
                if target == "cleanup":
                    self._gate_cleanup_children(db, job, review_id)
                self._set_result_id(db, job["id"], artifact_id)
            if job["kind"] == "review":
                self._refresh_search(db, job["entry_id"])
            elif needs_attention:
                _ = db.execute("UPDATE artifacts SET needs_attention=1 WHERE id=?", (artifact_id,))
            _ = db.execute(
                "UPDATE jobs SET status='done',finished_at=?,last_error=NULL,"
                "lease_until=NULL,claim_token=NULL,claim_protocol=0 WHERE id=?",
                (timestamp(), job["id"]),
            )
            self._refresh_search(db, job["entry_id"])
            from .embedding import EmbeddingService

            EmbeddingService(self, self.settings).reconcile(db, entry_ids={job["entry_id"]})
            return True

    @staticmethod
    def _set_result_id(db: sqlite3.Connection, job_id: str, artifact_id: str) -> None:
        _ = db.execute("UPDATE jobs SET result_artifact_id=? WHERE id=?", (artifact_id, job_id))

    @staticmethod
    def _producer_source_id(db: sqlite3.Connection, job: dict[str, Any], target: str) -> str:
        source_kind = ("raw_transcript", "user_text") if target == "cleanup" else ("cleaned",)
        for source_kind_item in source_kind:
            row = db.execute(
                "SELECT id FROM artifacts WHERE entry_id=? AND revision=? AND kind=? "
                "AND superseded_by IS NULL ORDER BY created_at DESC,id DESC LIMIT 1",
                (job["entry_id"], job["revision"], source_kind_item),
            ).fetchone()
            if row is not None:
                return str(row["id"])
        raise Conflict("Producer source artifact is unavailable")

    @staticmethod
    def _insert_session_job(
        db: sqlite3.Connection,
        parent: dict[str, Any],
        kind: str,
        target: str,
        turn: int,
        *,
        input_artifact_id: str,
        depends_on: str,
        review_artifact_id: str | None = None,
    ) -> str:
        job_id = identifier()
        _ = db.execute(
            "INSERT INTO jobs (id,entry_id,kind,target,turn,input_artifact_id,review_artifact_id,"
            "not_before,created_at,revision) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                parent["entry_id"],
                kind,
                target,
                turn,
                input_artifact_id,
                review_artifact_id,
                time.time(),
                timestamp(),
                parent["revision"],
            ),
        )
        _ = db.execute("INSERT INTO job_dependencies VALUES (?,?)", (job_id, depends_on))
        return job_id

    @staticmethod
    def _gate_cleanup_children(db: sqlite3.Connection, job: dict[str, Any], gate_id: str) -> None:
        cleanup = db.execute(
            "SELECT candidate_artifact_id FROM review_sessions WHERE entry_id=? "
            "AND revision=? AND target='cleanup'",
            (job["entry_id"], job["revision"]),
        ).fetchone()
        if cleanup is not None and cleanup["candidate_artifact_id"] is not None:
            _ = db.execute(
                "UPDATE review_sessions SET source_artifact_id=? WHERE entry_id=? "
                "AND revision=? AND target IN ('summarize','extract')",
                (cleanup["candidate_artifact_id"], job["entry_id"], job["revision"]),
            )
        _ = db.execute(
            "INSERT OR IGNORE INTO job_dependencies (job_id,depends_on) "
            "SELECT id,? FROM jobs WHERE entry_id=? AND revision=? "
            "AND status IN ('queued','running','failed') AND "
            "(kind IN ('summarize','extract') OR "
            "(kind IN ('review','revise') AND target IN ('summarize','extract'))) ",
            (gate_id, job["entry_id"], job["revision"]),
        )

    def requeue_interrupted(self, job: dict[str, Any]) -> bool:
        """Return a claimed job to the queue, due now, without consuming its attempt."""
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            if not self.batch_owned(db, job):
                return False
            result = db.execute(
                "UPDATE jobs SET status='queued',attempts=MAX(attempts-1,0),last_error=?,"
                "not_before=?,finished_at=NULL,lease_until=NULL,claim_token=NULL,"
                "claim_protocol=0 WHERE id=? AND status='running' AND claim_token=?",
                (
                    "Interrupted by worker shutdown; requeued without using an attempt",
                    time.time(),
                    job["id"],
                    job["claim_token"],
                ),
            )
            requeued = result.rowcount == 1
            if requeued and job["kind"] == "compile":
                _ = db.execute(
                    "UPDATE compilations SET status='queued' WHERE id=?", (job["compilation_id"],)
                )
            return requeued

    def fail_job(self, job: dict[str, Any], error: str, *, retryable: bool = True) -> bool:
        terminal = not retryable or job["attempts"] >= self.settings.max_attempts
        delay = self.settings.retry_base_s * 2 ** min(job["attempts"] - 1, 16)
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            if not self.batch_owned(db, job):
                return False
            result = db.execute(
                "UPDATE jobs SET status=?,last_error=?,not_before=?,finished_at=?,lease_until=NULL,"
                "claim_token=NULL,claim_protocol=0 "
                "WHERE id=? AND status='running' AND claim_token=?",
                (
                    "failed" if terminal else "queued",
                    error,
                    time.time() + delay,
                    timestamp() if terminal else None,
                    job["id"],
                    job["claim_token"],
                ),
            )
            if result.rowcount == 1 and job["kind"] == "compile":
                from .compilations import CompilationService

                CompilationService.fail_job(db, job["compilation_id"], error, terminal=terminal)
            return result.rowcount == 1

    @staticmethod
    def _refresh_search(db: sqlite3.Connection, entry_id: str) -> None:
        entry = Store.read_entry(db, entry_id, False)
        _ = db.execute("DELETE FROM search_index WHERE entry_id=?", (entry_id,))
        _ = db.execute(
            "INSERT INTO search_index VALUES (?,?,?,?,?)",
            (
                entry_id,
                entry["text"],
                entry["summary"] or "",
                entry["title"] or "",
                " ".join(entry["tags"]),
            ),
        )

    def search_documents(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT s.*,e.created_at FROM search_index s JOIN entries e ON e.id=s.entry_id "
                    "ORDER BY e.created_at DESC,e.id"
                )
            ]

    def fts_search(
        self, query: str, *, db: sqlite3.Connection | None = None
    ) -> list[dict[str, Any]]:
        # Quote each token to keep user input out of the FTS query language.
        tokens = query.split()
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        if not expression:
            return []
        if db is not None:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT entry_id,bm25(search_index,0,1,0.6,2,1.5) AS rank "
                    "FROM search_index WHERE search_index MATCH ? ORDER BY rank,entry_id",
                    (expression,),
                )
            ]
        with self.connect() as connection:
            return self.fts_search(query, db=connection)

    def set_token_hash(self, token_hash: str) -> None:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            _ = db.execute(
                "INSERT INTO auth VALUES (1,?) ON CONFLICT(id) "
                "DO UPDATE SET token_hash=excluded.token_hash",
                (token_hash,),
            )
            _ = db.execute("DELETE FROM sessions")

    def token_hash(self) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT token_hash FROM auth WHERE id=1").fetchone()
            return str(row[0]) if row is not None else None
