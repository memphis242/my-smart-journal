"""Transactional SQLite journal storage and immutable artifact lineage.

The Store interface is the seam for a future encrypted storage implementation.
"""

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import Settings


class NotFound(ValueError):
    """The requested resource does not exist."""


class Conflict(ValueError):
    """The requested transition is not valid for the resource's state."""


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
    def connect(self) -> Iterator[sqlite3.Connection]:
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
        self._drain_deletions()

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
    ) -> str:
        artifact_id = identifier()
        previous = db.execute(
            "SELECT id FROM artifacts WHERE entry_id=? AND kind=? AND superseded_by IS NULL",
            (entry_id, kind),
        ).fetchall()
        _ = db.execute(
            "INSERT INTO artifacts "
            "(id,entry_id,kind,content,model,prompt_sha256,created_at,revision) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (artifact_id, entry_id, kind, content, model, prompt_sha256, timestamp(), revision),
        )
        for row in previous:
            _ = db.execute(
                "UPDATE artifacts SET superseded_by=? WHERE id=?", (artifact_id, row["id"])
            )
        return artifact_id

    @staticmethod
    def _queue(db: sqlite3.Connection, entry_id: str, revision: int, audio: bool) -> None:
        kinds = (
            ["transcribe", "cleanup", "review", "summarize", "extract"]
            if audio
            else ["cleanup", "review", "summarize", "extract"]
        )
        ids = {kind: identifier() for kind in kinds}
        for kind, job_id in ids.items():
            _ = db.execute(
                "INSERT INTO jobs (id,entry_id,kind,not_before,created_at,revision) "
                "VALUES (?,?,?,?,?,?)",
                (job_id, entry_id, kind, time.time(), timestamp(), revision),
            )
            parent = {
                "cleanup": "transcribe",
                "review": "cleanup",
                "summarize": "cleanup",
                "extract": "cleanup",
            }.get(kind)
            if parent in ids:
                _ = db.execute("INSERT INTO job_dependencies VALUES (?,?)", (job_id, ids[parent]))

    def create_entry(
        self,
        *,
        text: str | None = None,
        audio: bytes | None = None,
        extension: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        entry_date: str | None = None,
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
        try:
            if audio is not None:
                digest = hashlib.sha256(audio).hexdigest()
                relative = Path("audio") / entry_id / f"{digest}.{extension}"
                stored_file = self.data_dir / relative
                (self.data_dir / "audio").mkdir(exist_ok=True, mode=0o700)
                stored_file.parent.mkdir(mode=0o700)
                _ = stored_file.write_bytes(audio)
                stored_file.chmod(0o600)
                content = str(relative)
            assert content is not None
            with self.connect() as db:
                _ = db.execute("BEGIN IMMEDIATE")
                _ = db.execute(
                    "INSERT INTO entries (id,created_at,entry_date,title,tags,source) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        entry_id,
                        timestamp(),
                        entry_date or date.today().isoformat(),
                        title,
                        json.dumps(tags or []),
                        "audio" if audio is not None else "text",
                    ),
                )
                _ = self._artifact(
                    db, entry_id, 1, "raw_audio" if audio is not None else "user_text", content
                )
                self._queue(db, entry_id, 1, audio is not None)
                self._refresh_search(db, entry_id)
        except Exception:
            if stored_file is not None:
                stored_file.unlink(missing_ok=True)
                stored_file.parent.rmdir()
            raise
        return self.get_entry(entry_id)

    @staticmethod
    def _read_entry(db: sqlite3.Connection, entry_id: str, history: bool) -> dict[str, Any]:
        entry = Store._entry(db, entry_id)
        rows = db.execute(
            "SELECT * FROM artifacts WHERE entry_id=? "
            + ("" if history else "AND revision=? AND superseded_by IS NULL ")
            + "ORDER BY created_at,id",
            (entry_id,) if history else (entry_id, entry["revision"]),
        ).fetchall()
        entry["artifacts"] = [
            dict(row) | {"needs_attention": bool(row["needs_attention"])} for row in rows
        ]
        entry["needs_attention"] = any(
            row["needs_attention"] for row in rows if row["revision"] == entry["revision"]
        )
        return entry

    def get_entry(self, entry_id: str, history: bool = False) -> dict[str, Any]:
        with self.connect() as db:
            _ = db.execute("BEGIN")
            return self._read_entry(db, entry_id, history)

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
            return [self._read_entry(db, entry_id, False) for entry_id in ids]

    def patch_entry(self, entry_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"title", "tags", "entry_date", "text"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("Patch must contain title, tags, entry_date or text")
        text = changes.get("text")
        if "text" in changes and (not isinstance(text, str) or not text.strip()):
            raise ValueError("Text must contain non-whitespace characters")
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            entry = self._entry(db, entry_id)
            for field in ("title", "tags", "entry_date"):
                if field in changes:
                    value = json.dumps(changes[field]) if field == "tags" else changes[field]
                    _ = db.execute(f"UPDATE entries SET {field}=? WHERE id=?", (value, entry_id))
            if text is not None:
                revision = int(entry["revision"]) + 1
                _ = db.execute("UPDATE entries SET revision=? WHERE id=?", (revision, entry_id))
                # Existing in-flight claims cannot publish into a newer revision.
                _ = db.execute(
                    "UPDATE jobs SET status='failed',"
                    "last_error='Entry text revised; use current jobs',"
                    "finished_at=?,claim_token=NULL,lease_until=NULL WHERE entry_id=? "
                    "AND status IN ('queued','running')",
                    (timestamp(), entry_id),
                )
                _ = self._artifact(db, entry_id, revision, "user_text", text)
                self._queue(db, entry_id, revision, False)
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
            _ = db.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        self._drain_deletions()

    def _drain_deletions(self) -> None:
        # Durable outbox: deletion failures can be retried at the next startup.
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            paths = [row[0] for row in db.execute("SELECT path FROM file_deletions")]
            for relative in paths:
                path = self.data_dir / relative
                path.unlink(missing_ok=True)
                if path.parent.exists() and not any(path.parent.iterdir()):
                    path.parent.rmdir()
                _ = db.execute("DELETE FROM file_deletions WHERE path=?", (relative,))

    def list_jobs(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in {"queued", "running", "done", "failed"}:
            raise ValueError("Unknown job status")
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM jobs"
                    + (" WHERE status=?" if status is not None else "")
                    + " ORDER BY created_at,id",
                    () if status is None else (status,),
                )
            ]

    def retry_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise NotFound("Job not found")
            entry = self._entry(db, job["entry_id"])
            if job["status"] != "failed" or job["revision"] != entry["revision"]:
                raise Conflict("Only failed jobs for the current entry revision can be retried")
            _ = db.execute(
                "UPDATE jobs SET status='queued',attempts=0,last_error=NULL,"
                "finished_at=NULL,not_before=? WHERE id=?",
                (time.time(), job_id),
            )
            result = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            assert result is not None
            return dict(result)

    def claim(self, eligible_ids: set[str]) -> dict[str, Any] | None:
        if not eligible_ids:
            return None
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            now = time.time()
            # A crashed worker's lease is recovered by the next batch.
            _ = db.execute(
                "UPDATE jobs SET status=CASE WHEN attempts>=? THEN 'failed' ELSE 'queued' END,"
                "last_error='Worker lease expired; retry after checking worker health',"
                "finished_at=CASE WHEN attempts>=? THEN ? ELSE NULL END,claim_token=NULL,"
                "lease_until=NULL WHERE status='running' AND lease_until<=?",
                (self.settings.max_attempts, self.settings.max_attempts, timestamp(), now),
            )
            rows = db.execute(
                "SELECT j.* FROM jobs j JOIN entries e ON e.id=j.entry_id "
                "WHERE j.status='queued' AND j.not_before<=? AND j.revision=e.revision "
                "AND NOT EXISTS (SELECT 1 FROM job_dependencies d JOIN jobs p ON p.id=d.depends_on "
                "WHERE d.job_id=j.id AND p.status!='done') ORDER BY j.created_at,j.id",
                (now,),
            ).fetchall()
            candidates = [dict(row) for row in rows if row["id"] in eligible_ids]
            if not candidates:
                return None

            def group(job: dict[str, Any]) -> tuple[int, str, str, str, str]:
                route = self.settings.tasks[job["kind"]]
                stage = {"transcribe": 0, "cleanup": 1}.get(job["kind"], 2)
                return (
                    stage,
                    route.provider,
                    route.endpoint_id or route.base_url,
                    route.model,
                    job["id"],
                )

            job = min(candidates, key=group)
            claim_token = identifier()
            _ = db.execute(
                "UPDATE jobs SET status='running',attempts=attempts+1,lease_until=?,"
                "claim_token=? WHERE id=?",
                (now + self.settings.lease_s, claim_token, job["id"]),
            )
            return job | {
                "status": "running",
                "attempts": job["attempts"] + 1,
                "claim_token": claim_token,
            }

    def job_inputs(self, job: dict[str, Any]) -> tuple[str, str | None, bytes | None]:
        with self.connect() as db:
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
            case "review":
                return raw, artifacts["cleaned"], None
            case _:
                return artifacts["cleaned"], None, None

    def finish_job(
        self,
        job: dict[str, Any],
        content: str,
        model: str | None,
        prompt_sha256: str | None,
        needs_attention: bool = False,
    ) -> bool:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
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
            }[job["kind"]]
            _ = self._artifact(
                db,
                job["entry_id"],
                job["revision"],
                kind,
                content,
                model=model,
                prompt_sha256=prompt_sha256,
            )
            if job["kind"] == "review":
                _ = db.execute(
                    "UPDATE artifacts SET needs_attention=? WHERE entry_id=? "
                    "AND revision=? AND kind='cleaned' AND superseded_by IS NULL",
                    (int(needs_attention), job["entry_id"], job["revision"]),
                )
            _ = db.execute(
                "UPDATE jobs SET status='done',finished_at=?,last_error=NULL,"
                "lease_until=NULL,claim_token=NULL WHERE id=?",
                (timestamp(), job["id"]),
            )
            self._refresh_search(db, job["entry_id"])
            return True

    def fail_job(self, job: dict[str, Any], error: str) -> bool:
        terminal = job["attempts"] >= self.settings.max_attempts
        delay = self.settings.retry_base_s * 2 ** min(job["attempts"] - 1, 16)
        with self.connect() as db:
            result = db.execute(
                "UPDATE jobs SET status=?,last_error=?,not_before=?,finished_at=?,lease_until=NULL,"
                "claim_token=NULL WHERE id=? AND status='running' AND claim_token=?",
                (
                    "failed" if terminal else "queued",
                    error,
                    time.time() + delay,
                    timestamp() if terminal else None,
                    job["id"],
                    job["claim_token"],
                ),
            )
            return result.rowcount == 1

    @staticmethod
    def _refresh_search(db: sqlite3.Connection, entry_id: str) -> None:
        entry = Store._entry(db, entry_id)
        texts = {
            row["kind"]: row["content"]
            for row in db.execute(
                "SELECT kind,content FROM artifacts WHERE entry_id=? AND revision=? "
                "AND superseded_by IS NULL",
                (entry_id, entry["revision"]),
            )
        }
        text = texts.get("cleaned", texts.get("user_text", texts.get("raw_transcript", "")))
        _ = db.execute("DELETE FROM search_index WHERE entry_id=?", (entry_id,))
        _ = db.execute(
            "INSERT INTO search_index VALUES (?,?,?,?,?)",
            (
                entry_id,
                text,
                texts.get("summary", ""),
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

    def fts_search(self, query: str) -> list[dict[str, Any]]:
        # Quote each token to keep user input out of the FTS query language.
        tokens = query.split()
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        if not expression:
            return []
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT entry_id,bm25(search_index,0,1,0.6,2,1.5) AS rank "
                    "FROM search_index WHERE search_index MATCH ? ORDER BY rank,entry_id",
                    (expression,),
                )
            ]

    def set_token_hash(self, token_hash: str) -> None:
        with self.connect() as db:
            _ = db.execute(
                "INSERT INTO auth VALUES (1,?) ON CONFLICT(id) "
                "DO UPDATE SET token_hash=excluded.token_hash",
                (token_hash,),
            )

    def token_hash(self) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT token_hash FROM auth WHERE id=1").fetchone()
            return str(row[0]) if row is not None else None
