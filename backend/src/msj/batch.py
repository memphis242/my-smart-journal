"""SQLite-backed single-flight batch leases and queue snapshots."""

import json
import logging
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from .diagnostics import SafeMessageError, safe_exception
from .ownership import OwnerIdentity
from .storage import Conflict, Store, identifier, timestamp

if TYPE_CHECKING:
    from .pipeline import Runner

_logger = logging.getLogger(__name__)
Trigger = Literal["manual", "schedule"]


class BatchStateUnavailable(SafeMessageError):
    code = "BATCH_STATE_UNAVAILABLE"


def _safe_detail(diagnostic: dict[str, Any]) -> str:
    message = diagnostic.get("message")
    if isinstance(message, str):
        return message
    details = [str(diagnostic.get("type", "Exception"))]
    for key in ("errno", "filename", "sqlite_errorcode", "sqlite_errorname"):
        value = diagnostic.get(key)
        if value is not None:
            details.append(f"{key}={value}")
    validation = diagnostic.get("validation")
    if validation:
        details.append(f"validation={validation}")
    frames = diagnostic.get("frames")
    if frames:
        details.append(f"at {frames[-1]}")
    return (
        "Batch failed; inspect worker logs for bounded diagnostic details ("
        + ", ".join(details)
        + ")."
    )


class BatchRequestCoordinator:
    def __init__(self, store: Store) -> None:
        self._store = store

    def request(self) -> bool:
        """Persist a manual request; return false when a batch currently owns the lease."""
        now = time.time()
        with self._store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM batch_state WHERE id=1").fetchone()
            if row is None:
                raise BatchStateUnavailable(
                    "Batch state is unavailable; run `msj migrate` and restart"
                )
            if (
                row["batch_id"] is not None
                and row["lease_until"] is not None
                and float(row["lease_until"]) > now
                and OwnerIdentity.alive(row)
            ):
                return False
            _ = db.execute(
                "UPDATE batch_state SET request_pending=1,request_created_at=COALESCE("
                "request_created_at,?),worker_next_run_at=? WHERE id=1",
                (timestamp(), timestamp()),
            )
        return True

    def status(self) -> dict[str, Any]:
        return queue_status(self._store)

    def has_pending_request(self) -> bool:
        return self.pending_request_at() is not None

    def pending_request_at(self) -> str | None:
        with self._store.connect() as db:
            row = db.execute(
                "SELECT request_pending,request_created_at FROM batch_state WHERE id=1"
            ).fetchone()
        if row is None:
            raise BatchStateUnavailable("Batch state is unavailable; run `msj migrate` and restart")
        return str(row["request_created_at"]) if row["request_pending"] else None

    def heartbeat_worker(
        self, next_run_at: str | None = None, *, observed_request_at: str | None = None
    ) -> None:
        with self._store.connect() as db:
            if next_run_at is None:
                _ = db.execute(
                    "UPDATE batch_state SET worker_last_seen_at=? WHERE id=1", (timestamp(),)
                )
            else:
                _ = db.execute(
                    "UPDATE batch_state SET worker_last_seen_at=?,worker_next_run_at="
                    "CASE WHEN request_pending=1 AND request_created_at IS NOT ? "
                    "THEN COALESCE(worker_next_run_at,?) ELSE ? END "
                    "WHERE id=1",
                    (timestamp(), observed_request_at, timestamp(), next_run_at),
                )

    def touch_worker(self) -> None:
        self.heartbeat_worker()


class BatchCoordinator(BatchRequestCoordinator):
    def __init__(self, store: Store, runner: Runner) -> None:
        super().__init__(store)
        self._runner = runner

    def _owner(self) -> OwnerIdentity:
        return OwnerIdentity.current()

    def acquire(self, trigger: Trigger) -> str | None:
        batch_id = identifier()
        now = time.time()
        owner = self._owner()
        with self._store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM batch_state WHERE id=1").fetchone()
            if row is None:
                raise BatchStateUnavailable(
                    "Batch state is unavailable; run `msj migrate` and restart"
                )
            active = row["batch_id"] is not None and row["lease_until"] is not None
            if active and float(row["lease_until"]) > now and OwnerIdentity.alive(row):
                return None
            if active:
                _ = db.execute(
                    "UPDATE batch_state SET last_error=? WHERE id=1",
                    (
                        json.dumps(
                            {
                                "code": "BATCH_LEASE_LOST",
                                "detail": "The previous batch owner stopped renewing its lease.",
                            }
                        ),
                    ),
                )
            _ = db.execute(
                "UPDATE batch_state SET batch_id=?,started_at=?,trigger=?,lease_until=?,"
                "owner_host=?,owner_pid=?,owner_boot_id=?,owner_started_at=? WHERE id=1",
                (
                    batch_id,
                    timestamp(),
                    trigger,
                    now + self._store.settings.lease_s,
                    owner.host,
                    owner.pid,
                    owner.boot_id,
                    owner.started_at,
                ),
            )
        return batch_id

    def renew(self, batch_id: str) -> None:
        now = time.time()
        with self._store.connect() as db:
            result = db.execute(
                "UPDATE batch_state SET lease_until=? WHERE id=1 AND batch_id=? AND lease_until>?",
                (now + self._store.settings.lease_s, batch_id, now),
            )
            if result.rowcount != 1:
                raise Conflict("Batch lease expired; retry the batch after checking worker health")

    def release(
        self,
        batch_id: str,
        result: dict[str, Any] | None,
        *,
        error: dict[str, str] | None = None,
    ) -> None:
        persisted_result = (
            {"processed": int(result.get("done", 0)), "failed": int(result.get("failed", 0))}
            if result is not None
            else None
        )
        with self._store.connect() as db:
            changed = db.execute(
                "UPDATE batch_state SET batch_id=NULL,started_at=NULL,trigger=NULL,"
                "lease_until=NULL,owner_host=NULL,owner_pid=NULL,owner_boot_id=NULL,"
                "owner_started_at=NULL,request_pending=0,request_created_at=NULL,"
                "last_finished_at=?,last_result=?,last_error=? WHERE id=1 AND batch_id=?",
                (
                    timestamp(),
                    json.dumps(persisted_result) if persisted_result is not None else None,
                    json.dumps(error) if error is not None else None,
                    batch_id,
                ),
            )
            lost = changed.rowcount != 1
        if lost:
            lost_error = json.dumps(
                {
                    "code": "BATCH_LEASE_LOST",
                    "detail": "The batch lease was replaced before this batch could finish.",
                }
            )
            with self._store.connect() as db:
                _ = db.execute(
                    "UPDATE batch_state SET last_error=? WHERE id=1 AND last_error IS NULL",
                    (lost_error,),
                )
            raise Conflict("Batch lease was replaced before release; owner no longer matches")

    def run_sync(self, trigger: Trigger = "manual", *, worker: bool = False) -> dict[str, Any]:
        batch_id = self.acquire(trigger)
        if batch_id is None:
            raise Conflict("A batch is already running; wait for it to finish before retrying")
        return self._run_claimed(batch_id, worker=worker)

    def _run_claimed(self, batch_id: str, *, worker: bool) -> dict[str, Any]:
        try:
            result = self._execute_with_renewal(batch_id, worker=worker)
        except Exception as exc:
            failure = safe_exception(exc)
            error_code = "BATCH_LEASE_LOST" if isinstance(exc, Conflict) else "BATCH_FAILED"
            try:
                self.release(
                    batch_id,
                    None,
                    error={"code": error_code, "detail": _safe_detail(failure)},
                )
            except Exception as release_exc:  # noqa: BLE001 - the original failure is re-raised
                _logger.error(
                    "Failed batch error could not be persisted during lease release: %s",
                    safe_exception(release_exc),
                )
            raise
        self.release(batch_id, result)
        return result

    def _execute_with_renewal(self, batch_id: str, *, worker: bool = False) -> dict[str, Any]:
        stop = threading.Event()
        lease_lost = threading.Event()

        def renew_until_done() -> None:
            interval = min(
                max(0.2, self._store.settings.worker_tick_s),
                max(0.2, self._store.settings.lease_s / 3),
            )
            while not stop.wait(interval):
                if worker:
                    try:
                        self.touch_worker()
                    except Exception as exc:  # noqa: BLE001 - a missed heartbeat must not stop work
                        _logger.warning("Worker heartbeat update failed: %s", safe_exception(exc))
                try:
                    self.renew(batch_id)
                except Conflict:
                    lease_lost.set()
                    _logger.error("Batch lease renewal failed code=BATCH_LEASE_LOST")
                    return
                except Exception as exc:  # noqa: BLE001 - renewal retries until the lease is lost
                    failure = safe_exception(exc)
                    _logger.warning("Transient batch lease renewal failure: %s", failure)

        heartbeat = threading.Thread(target=renew_until_done, name="msj-batch-lease", daemon=True)
        heartbeat.start()
        try:
            result = self._runner.run_batch(_lease_id=batch_id)
        finally:
            stop.set()
            heartbeat.join()
        if lease_lost.is_set():
            raise Conflict("Batch lease was lost while processing")
        return result

    def run_requested(self) -> dict[str, Any] | None:
        now = time.time()
        batch_id = identifier()
        owner = self._owner()
        with self._store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM batch_state WHERE id=1").fetchone()
            if row is None:
                raise BatchStateUnavailable(
                    "Batch state is unavailable; run `msj migrate` and restart"
                )
            if not row["request_pending"]:
                return None
            if row["batch_id"] is not None and row["lease_until"] is not None:
                if float(row["lease_until"]) > now and OwnerIdentity.alive(row):
                    raise Conflict("A batch is already running; retry after its lease finishes")
                _ = db.execute(
                    "UPDATE batch_state SET last_error=? WHERE id=1",
                    (
                        json.dumps(
                            {
                                "code": "BATCH_LEASE_LOST",
                                "detail": "The previous batch owner stopped renewing its lease.",
                            }
                        ),
                    ),
                )
            _ = db.execute(
                "UPDATE batch_state SET batch_id=?,started_at=?,trigger='manual',lease_until=?,"
                "owner_host=?,owner_pid=?,owner_boot_id=?,owner_started_at=? WHERE id=1",
                (
                    batch_id,
                    timestamp(),
                    now + self._store.settings.lease_s,
                    owner.host,
                    owner.pid,
                    owner.boot_id,
                    owner.started_at,
                ),
            )
        return self._run_claimed(batch_id, worker=True)


def queue_status(store: Store) -> dict[str, Any]:
    now = time.time()
    with store.connect() as db:
        state = db.execute("SELECT * FROM batch_state WHERE id=1").fetchone()
        if state is None:
            raise BatchStateUnavailable("Queue state is unavailable; run `msj migrate` and restart")
        current = "(j.entry_id IS NULL OR j.revision=e.revision)"
        status_counts = {
            str(row["status"]): int(row["count"])
            for row in db.execute(
                "SELECT j.status,COUNT(*) AS count FROM jobs j LEFT JOIN entries e "  # noqa: S608 - fixed SQL fragment
                f"ON e.id=j.entry_id WHERE {current} GROUP BY j.status"
            )
        }
        blocked = int(
            db.execute(
                "WITH RECURSIVE ancestors(job_id,ancestor_id) AS ("  # noqa: S608 - fixed SQL fragment
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
    batch_running = state["batch_id"] is not None and state["lease_until"] is not None
    batch_running = bool(
        batch_running and float(state["lease_until"]) > now and OwnerIdentity.alive(state)
    )
    result = json.loads(state["last_result"]) if state["last_result"] else None
    last_error = json.loads(state["last_error"]) if state["last_error"] else None
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
            "last_error": last_error,
            "request_pending": bool(state["request_pending"]),
        },
        "worker": {
            "last_seen_at": state["worker_last_seen_at"],
            "stale": worker_stale(state["worker_last_seen_at"], store.settings.worker_tick_s),
            "interval_s": store.settings.batch_interval_s,
            "next_run_at": state["worker_next_run_at"],
            "next_batch_at": state["worker_next_run_at"],
        },
        "counts": counts,
        "items": items,
    }


def worker_stale(last_seen_at: str | None, tick_s: float) -> bool:
    """Return whether the worker heartbeat is missing or older than three ticks (10 s minimum)."""
    assert tick_s > 0
    if last_seen_at is None:
        return True
    seen = datetime.fromisoformat(last_seen_at)
    return (datetime.now(UTC) - seen).total_seconds() > max(10.0, tick_s * 3)


def _timestamp_from_epoch(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, UTC).isoformat()
