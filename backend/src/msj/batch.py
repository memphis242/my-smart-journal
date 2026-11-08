"""SQLite-backed single-flight batch leases and queue snapshots."""

import json
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from .storage import Conflict, Store, identifier, timestamp

if TYPE_CHECKING:
    from .pipeline import Runner

_logger = logging.getLogger(__name__)
Trigger = Literal["manual", "schedule"]


class BatchCoordinator:
    def __init__(self, store: Store, runner: Runner) -> None:
        self._store = store
        self._runner = runner

    def acquire(self, trigger: Trigger) -> str | None:
        batch_id = identifier()
        now = time.time()
        with self._store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT batch_id,lease_until FROM batch_state WHERE id=1").fetchone()
            if row is None:
                raise RuntimeError("Batch state is unavailable; run `msj migrate` and restart")
            if row["batch_id"] is not None and row["lease_until"] is not None:
                if float(row["lease_until"]) > now:
                    return None
            _ = db.execute(
                "UPDATE batch_state SET batch_id=?,started_at=?,trigger=?,lease_until=? WHERE id=1",
                (batch_id, timestamp(), trigger, now + self._store.settings.lease_s),
            )
        return batch_id

    def status(self) -> dict[str, Any]:
        return queue_status(self._store)

    def heartbeat_worker(self) -> None:
        with self._store.connect() as db:
            _ = db.execute(
                "UPDATE batch_state SET worker_last_seen_at=? WHERE id=1", (timestamp(),)
            )

    def renew(self, batch_id: str) -> None:
        now = time.time()
        with self._store.connect() as db:
            result = db.execute(
                "UPDATE batch_state SET lease_until=? "
                "WHERE id=1 AND batch_id=? AND lease_until>?",
                (now + self._store.settings.lease_s, batch_id, now),
            )
            if result.rowcount != 1:
                raise Conflict("Batch lease expired; retry the batch after checking worker health")

    def release(self, batch_id: str, result: dict[str, Any] | None) -> None:
        persisted_result = (
            {"processed": int(result.get("done", 0)), "failed": int(result.get("failed", 0))}
            if result is not None
            else None
        )
        with self._store.connect() as db:
            _ = db.execute(
                "UPDATE batch_state SET batch_id=NULL,started_at=NULL,trigger=NULL,"
                "lease_until=NULL,"
                "last_finished_at=?,last_result=? WHERE id=1 AND batch_id=?",
                (
                    timestamp(),
                    json.dumps(persisted_result) if persisted_result is not None else None,
                    batch_id,
                ),
            )

    def run_sync(self, trigger: Trigger = "manual") -> dict[str, Any]:
        batch_id = self.acquire(trigger)
        if batch_id is None:
            raise Conflict("A batch is already running; wait for it to finish before retrying")
        try:
            result = self._execute_with_renewal(batch_id)
        except Exception:
            self.release(batch_id, None)
            raise
        self.release(batch_id, result)
        return result

    def start(self, trigger: Trigger = "manual") -> str | None:
        batch_id = self.acquire(trigger)
        if batch_id is None:
            return None
        thread = threading.Thread(
            target=self._run_background,
            args=(batch_id,),
            name="msj-batch",
            daemon=True,
        )
        try:
            thread.start()
        except RuntimeError:
            self.release(batch_id, None)
            raise
        return batch_id

    def _execute_with_renewal(self, batch_id: str) -> dict[str, Any]:
        stop = threading.Event()

        def renew_until_done() -> None:
            interval = max(1.0, self._store.settings.lease_s / 3)
            while not stop.wait(interval):
                try:
                    self.renew(batch_id)
                except Exception as exc:
                    _logger.error("Batch lease renewal failed (%s)", type(exc).__name__)
                    return

        heartbeat = threading.Thread(target=renew_until_done, name="msj-batch-lease", daemon=True)
        heartbeat.start()
        try:
            return self._runner.run_batch(_lease_id=batch_id)
        finally:
            stop.set()
            heartbeat.join(timeout=1)

    def _run_background(self, batch_id: str) -> None:
        try:
            result = self._execute_with_renewal(batch_id)
        except Exception as exc:
            _logger.error(
                "Background batch failed (%s); check persisted job errors and logs",
                type(exc).__name__,
            )
            self.release(batch_id, None)
        else:
            self.release(batch_id, result)


def queue_status(store: Store) -> dict[str, Any]:
    now = time.time()
    with store.connect() as db:
        state = db.execute("SELECT * FROM batch_state WHERE id=1").fetchone()
        if state is None:
            raise RuntimeError("Queue state is unavailable; run `msj migrate` and restart")
        current = "(j.entry_id IS NULL OR j.revision=e.revision)"
        status_counts = {
            str(row["status"]): int(row["count"])
            for row in db.execute(
                "SELECT j.status,COUNT(*) AS count FROM jobs j LEFT JOIN entries e "
                f"ON e.id=j.entry_id WHERE {current} GROUP BY j.status"
            )
        }
        blocked = int(
            db.execute(
                "WITH RECURSIVE ancestors(job_id,ancestor_id) AS ("
                "SELECT job_id,depends_on FROM job_dependencies UNION "
                "SELECT a.job_id,d.depends_on FROM ancestors a "
                "JOIN job_dependencies d ON d.job_id=a.ancestor_id) "
                "SELECT COUNT(DISTINCT j.id) FROM jobs j LEFT JOIN entries e ON e.id=j.entry_id "
                f"JOIN ancestors a ON a.job_id=j.id JOIN jobs p ON p.id=a.ancestor_id "
                f"WHERE {current} AND j.status='queued' AND p.status='failed'"
            ).fetchone()[0]
        )
        counts = {
            "queued": status_counts.get("queued", 0),
            "running": status_counts.get("running", 0),
            "failed": status_counts.get("failed", 0),
            "blocked": blocked,
        }
    last_seen = state["worker_last_seen_at"]
    batch_running = state["batch_id"] is not None and state["lease_until"] is not None
    batch_running = bool(batch_running and float(state["lease_until"]) > now)
    result = json.loads(state["last_result"]) if state["last_result"] else None
    jobs = store.list_jobs()
    with store.connect() as db:
        current_ids = {
            str(row["id"])
            for row in db.execute(
                "SELECT j.id FROM jobs j LEFT JOIN entries e ON e.id=j.entry_id "
                "WHERE j.entry_id IS NULL OR j.revision=e.revision"
            )
        }
    items: list[dict[str, Any]] = []
    with store.connect() as db:
        for job in jobs:
            if job["id"] not in current_ids or job["status"] not in {"queued", "running", "failed"}:
                continue
            entry = None
            if job.get("entry_id") is not None:
                entry = db.execute(
                    "SELECT title,entry_date FROM entries WHERE id=?", (job["entry_id"],)
                ).fetchone()
            items.append(
                {
                    "entry_id": job.get("entry_id"),
                    "entry_title": entry["title"] if entry is not None else None,
                    "entry_date": entry["entry_date"] if entry is not None else None,
                    "job_id": job["id"],
                    "kind": job["kind"],
                    "target": job.get("target") or None,
                    "turn": job.get("turn") or None,
                    "status": job["status"],
                    "blocked_by": job.get("blocked_by", []),
                    "last_error": job.get("last_error"),
                    "not_before": _timestamp_from_epoch(job.get("not_before")),
                }
            )
            if len(items) == 200:
                break
    return {
        "batch": {
            "running": batch_running,
            "started_at": state["started_at"] if batch_running else None,
            "trigger": state["trigger"] if batch_running else None,
            "last_finished_at": state["last_finished_at"],
            "last_result": result,
        },
        "worker": {
            "last_seen_at": last_seen,
            "interval_s": store.settings.batch_interval_s,
            "next_run_at": (
                _next_run(last_seen, store.settings.batch_interval_s) if last_seen else None
            ),
        },
        "counts": counts,
        "items": items,
    }


def _next_run(last_seen_at: str, interval_s: float) -> str | None:
    try:
        parsed = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed + timedelta(seconds=interval_s)).isoformat()


def _timestamp_from_epoch(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, UTC).isoformat()
