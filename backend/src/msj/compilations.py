"""Compilation records and immutable source snapshots."""

import json
import sqlite3
import time
from typing import Any

from .entry_views import final_artifact, summary_fields
from .storage import Conflict, NotFound, Store, identifier, timestamp


class CompilationService:
    def __init__(self, store: Store) -> None:
        self._store = store

    def create(self, entry_ids: list[str], purpose: str, period: str) -> dict[str, Any]:
        if not entry_ids or len(entry_ids) > 1000:
            raise ValueError("Provide between 1 and 1000 entry ids for compilation")
        if not purpose.strip() or not period.strip():
            raise ValueError("Compilation purpose and period must not be empty")
        compilation_id = identifier()
        with self._store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            unique_ids = list(dict.fromkeys(entry_ids))
            placeholders = ",".join("?" for _ in unique_ids)
            rows = db.execute(
                "SELECT id,entry_date,title,tags,revision FROM entries "
                f"WHERE id IN ({placeholders})",
                unique_ids,
            ).fetchall()
            by_id = {str(row["id"]): row for row in rows}
            missing = [entry_id for entry_id in unique_ids if entry_id not in by_id]
            if missing:
                raise NotFound("Compilation entries not found: " + ", ".join(missing[:20]))
            created_at = timestamp()
            _ = db.execute(
                "INSERT INTO compilations "
                "(id,created_at,status,entry_ids,source_content,purpose,period) "
                "VALUES (?,?, 'queued', ?,?,?,?)",
                (
                    compilation_id,
                    created_at,
                    json.dumps(unique_ids),
                    "null",
                    purpose,
                    period,
                ),
            )
            _ = db.execute(
                "INSERT INTO jobs "
                "(id,compilation_id,kind,target,turn,not_before,created_at,revision) "
                "VALUES (?,?, 'compile','',0,?,?,0)",
                (identifier(), compilation_id, time.time(), created_at),
            )
            CompilationService.reconcile(db)
        return self.get(compilation_id)

    def get(self, compilation_id: str) -> dict[str, Any]:
        with self._store.connect() as db:
            row = db.execute("SELECT * FROM compilations WHERE id=?", (compilation_id,)).fetchone()
            if row is None:
                raise NotFound("Compilation not found")
            return self._view(row)

    def list(self) -> list[dict[str, Any]]:
        with self._store.connect() as db:
            return [
                self._view(row)
                for row in db.execute("SELECT * FROM compilations ORDER BY created_at DESC,id")
            ]

    def delete(self, compilation_id: str) -> None:
        with self._store.connect() as db:
            result = db.execute("DELETE FROM compilations WHERE id=?", (compilation_id,))
            if result.rowcount != 1:
                raise NotFound("Compilation not found")

    @staticmethod
    def job_inputs(db: sqlite3.Connection, compilation_id: str) -> str:
        row = db.execute(
            "SELECT source_content,purpose,period,entry_ids FROM compilations WHERE id=?",
            (compilation_id,),
        ).fetchone()
        if row is None:
            raise NotFound("Compilation source is unavailable; retry with existing entries")
        if row["source_content"] == "null":
            job = db.execute(
                "SELECT id,status FROM jobs WHERE compilation_id=? AND kind='compile'",
                (compilation_id,),
            ).fetchone()
            if job is None or job["status"] != "queued":
                raise Conflict(
                    "Compilation input snapshot is unavailable; retry the compilation after its "
                    "entry jobs finish"
                )
            unfinished = db.execute(
                "SELECT 1 FROM job_dependencies d JOIN jobs p ON p.id=d.depends_on "
                "WHERE d.job_id=? AND p.status!='done' LIMIT 1",
                (job["id"],),
            ).fetchone()
            if unfinished is not None:
                raise Conflict("Compilation is waiting for selected entries to finish processing")
            entry_ids = json.loads(row["entry_ids"])
            source: list[dict[str, Any]] = []
            for entry_id in entry_ids:
                if not isinstance(entry_id, str):
                    raise Conflict("Compilation contains an invalid entry id")
                entry_row = db.execute("SELECT 1 FROM entries WHERE id=?", (entry_id,)).fetchone()
                if entry_row is None:
                    raise NotFound(
                        f"Compilation entry {entry_id} was deleted before input snapshot creation"
                    )
                entry = Store._read_entry(db, entry_id, False)
                fields = summary_fields(entry)
                extraction = final_artifact(entry, "extraction")
                try:
                    extraction_content = json.loads(extraction["content"]) if extraction else None
                except (ValueError, TypeError, RecursionError):
                    extraction_content = None
                source.append(
                    {
                        "entry_date": entry["entry_date"],
                        "title": entry["title"],
                        "tags": entry["tags"],
                        "text": fields["text"],
                        "summary": fields["summary"],
                        "extraction": extraction_content,
                    }
                )
            source_content = json.dumps(source, ensure_ascii=False)
            updated = db.execute(
                "UPDATE compilations SET source_content=? WHERE id=? AND source_content='null'",
                (source_content, compilation_id),
            )
            assert updated.rowcount == 1
            row = dict(row) | {"source_content": source_content}
        return json.dumps(
            {
                "entries": json.loads(row["source_content"]),
                "purpose": row["purpose"],
                "period": row["period"],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def reconcile(db: sqlite3.Connection) -> None:
        """Fence queued compile jobs behind all current selected entry work."""
        rows = db.execute(
            "SELECT c.id,c.entry_ids,j.id AS job_id FROM compilations c "
            "JOIN jobs j ON j.compilation_id=c.id AND j.kind='compile' "
            "WHERE c.status='queued' AND c.source_content='null' AND j.status='queued' "
            "ORDER BY c.created_at,c.id"
        ).fetchall()
        for row in rows:
            _ = db.execute("DELETE FROM job_dependencies WHERE job_id=?", (row["job_id"],))
            entry_ids = json.loads(row["entry_ids"])
            if not isinstance(entry_ids, list) or not all(
                isinstance(entry_id, str) for entry_id in entry_ids
            ):
                raise RuntimeError("Compilation entry id snapshot is invalid")
            if not entry_ids:
                continue
            placeholders = ",".join("?" for _ in entry_ids)
            prerequisites = db.execute(
                "SELECT j.id FROM jobs j JOIN entries e ON e.id=j.entry_id "
                f"WHERE j.entry_id IN ({placeholders}) AND j.revision=e.revision "
                "AND j.kind IN ('transcribe','cleanup','summarize','extract','review','revise') "
                "AND j.status!='superseded'",
                entry_ids,
            ).fetchall()
            for prerequisite in prerequisites:
                _ = db.execute(
                    "INSERT OR IGNORE INTO job_dependencies (job_id,depends_on) VALUES (?,?)",
                    (row["job_id"], prerequisite["id"]),
                )

    @staticmethod
    def finish_job(
        db: sqlite3.Connection,
        compilation_id: str,
        content: str,
        model: str | None,
        prompt_sha256: str,
    ) -> None:
        _ = db.execute(
            "UPDATE compilations "
            "SET status='done',result=?,model=?,prompt_sha256=?,last_error=NULL "
            "WHERE id=?",
            (content, model, prompt_sha256, compilation_id),
        )

    @staticmethod
    def fail_job(
        db: sqlite3.Connection,
        compilation_id: str,
        error: str,
        *,
        terminal: bool,
    ) -> None:
        _ = db.execute(
            "UPDATE compilations SET status=?,last_error=? WHERE id=?",
            ("failed" if terminal else "queued", error, compilation_id),
        )

    @staticmethod
    def _view(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "status": row["status"],
            "entry_ids": json.loads(row["entry_ids"]),
            "purpose": row["purpose"],
            "period": row["period"],
            "result": json.loads(row["result"]) if row["result"] is not None else None,
            "last_error": row["last_error"],
            "model": row["model"],
            "prompt_sha256": row["prompt_sha256"],
        }

