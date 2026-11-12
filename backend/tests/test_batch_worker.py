from __future__ import annotations

import os
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from msj.batch import BatchCoordinator, BatchRequestCoordinator
from msj.config import TASKS, Settings, TaskRoute
from msj.ownership import OwnerIdentity
from msj.pipeline import Runner
from msj.storage import Conflict, Store


def _coordinator(tmp_path: Path) -> BatchCoordinator:
    settings = Settings(
        data_dir=tmp_path,
        embedding={"provider": "fake"},
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    return BatchCoordinator(store, Runner(store, settings))


def test_manual_batch_request_is_durable_and_idempotent(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)

    assert coordinator.request()
    assert coordinator.request()
    assert coordinator.status()["batch"]["request_pending"] is True

    result = coordinator.run_requested()

    assert result is not None
    assert coordinator.status()["batch"]["request_pending"] is False


def test_manual_request_survives_worker_crash_after_lease_acquisition(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    assert coordinator.request()
    with coordinator._store.connect() as db:
        _ = db.execute(
            "UPDATE batch_state SET batch_id='crashed',lease_until=?,owner_host=?,"
            "owner_pid=?,owner_boot_id=?,owner_started_at=? WHERE id=1",
            (10**12, socket.gethostname(), 2**30, OwnerIdentity.current().boot_id, "old-process"),
        )

    assert coordinator.status()["batch"]["running"] is False
    result = coordinator.run_requested()

    assert result is not None
    assert coordinator.status()["batch"]["request_pending"] is False


def test_worker_heartbeat_preserves_immediate_manual_request_deadline(tmp_path: Path) -> None:
    request_coordinator = BatchRequestCoordinator(
        Store(
            Settings(
                data_dir=tmp_path,
                tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
            )
        )
    )
    request_coordinator._store.migrate()
    assert request_coordinator.request()
    with request_coordinator._store.connect() as db:
        request_deadline = db.execute(
            "SELECT worker_next_run_at FROM batch_state WHERE id=1"
        ).fetchone()[0]

    request_coordinator.heartbeat_worker((datetime.now(UTC) + timedelta(hours=1)).isoformat())

    with request_coordinator._store.connect() as db:
        published_deadline = db.execute(
            "SELECT worker_next_run_at FROM batch_state WHERE id=1"
        ).fetchone()[0]
    assert published_deadline == request_deadline


def test_worker_heartbeat_publishes_retry_for_observed_request(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    assert coordinator.request()
    observed = coordinator.pending_request_at()
    assert observed is not None
    retry = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
    coordinator.heartbeat_worker(retry, observed_request_at=observed)
    assert coordinator.status()["worker"]["next_run_at"] == retry
    coordinator.touch_worker()
    assert coordinator.status()["worker"]["next_run_at"] == retry

    assert coordinator.run_requested() is not None
    assert coordinator.request()
    immediate = coordinator.status()["worker"]["next_run_at"]
    coordinator.heartbeat_worker(retry, observed_request_at=observed)
    assert coordinator.status()["worker"]["next_run_at"] == immediate


def test_dead_local_lease_can_be_reclaimed(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    store = coordinator._store
    with store.connect() as db:
        _ = db.execute(
            "UPDATE batch_state SET batch_id='crashed',lease_until=?,owner_host=?,"
            "owner_pid=?,owner_boot_id=?,owner_started_at=? WHERE id=1",
            (
                10**12,
                socket.gethostname(),
                2**30,
                OwnerIdentity.current().boot_id,
                "2026-01-01T00:00:00+00:00",
            ),
        )

    replacement = coordinator.acquire("manual")

    assert replacement is not None
    with store.connect() as db:
        row = db.execute("SELECT batch_id,owner_pid FROM batch_state WHERE id=1").fetchone()
    assert row["batch_id"] == replacement
    assert row["owner_pid"] == os.getpid()


def test_replaced_owner_persists_lease_lost_error(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    previous = coordinator.acquire("manual")
    assert previous is not None
    with coordinator._store.connect() as db:
        _ = db.execute("UPDATE batch_state SET lease_until=0 WHERE id=1")
    replacement = coordinator.acquire("manual")
    assert replacement is not None

    with pytest.raises(Conflict, match="lease was replaced"):
        coordinator.release(previous, None)

    batch = coordinator.status()["batch"]
    assert batch["running"] is True
    assert batch["last_error"]["code"] == "BATCH_LEASE_LOST"


def test_transient_renew_and_heartbeat_errors_do_not_stop_renewal(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator._store.settings = coordinator._store.settings.model_copy(
        update={"worker_tick_s": 0.01}
    )
    renewed = threading.Event()
    counts = {"renew": 0, "touch": 0}

    def renew(_batch_id: str) -> None:
        counts["renew"] += 1
        if counts["renew"] == 1:
            raise sqlite3.OperationalError("database is locked")
        renewed.set()

    def touch_worker() -> None:
        counts["touch"] += 1
        if counts["touch"] == 1:
            raise sqlite3.OperationalError("database is locked")

    class WaitingRunner:
        def run_batch(self, *, _lease_id: str) -> dict[str, int]:
            assert renewed.wait(2)
            return {"done": 0, "failed": 0}

    coordinator._runner = WaitingRunner()  # type: ignore[assignment]
    coordinator.renew = renew  # type: ignore[method-assign]
    coordinator.touch_worker = touch_worker  # type: ignore[method-assign]

    result = coordinator._execute_with_renewal("test", worker=True)

    assert result["done"] == 0
    assert counts == {"renew": 2, "touch": 2}


@pytest.mark.parametrize("expire", [False, True])
def test_heartbeat_shutdown_waits_for_inflight_renewal(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    expire: bool,
) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator._store.settings = coordinator._store.settings.model_copy(
        update={"worker_tick_s": 0.01}
    )
    started = threading.Event()
    resume = threading.Event()
    original_renew = coordinator.renew

    def delayed_renew(batch_id: str) -> None:
        started.set()
        assert resume.wait(3)
        if expire:
            with coordinator._store.connect() as db:
                _ = db.execute("UPDATE batch_state SET lease_until=0 WHERE id=1")
        original_renew(batch_id)

    class ImmediateRunner:
        def run_batch(self, *, _lease_id: str) -> dict[str, int]:
            assert started.wait(2)
            return {"done": 0, "failed": 0}

    coordinator._runner = ImmediateRunner()  # type: ignore[assignment]
    coordinator.renew = delayed_renew  # type: ignore[method-assign]
    release_renewal = threading.Timer(1.2, resume.set)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(coordinator.run_sync)
            assert started.wait(2)
            release_renewal.start()
            assert not future.done()
            if expire:
                with pytest.raises(Conflict, match="lease was lost"):
                    _ = future.result(timeout=3)
            else:
                assert future.result(timeout=3)["done"] == 0
                assert resume.is_set(), "Lease release must wait for in-flight renewal"
    finally:
        release_renewal.cancel()
        resume.set()

    assert ("Batch lease renewal failed code=BATCH_LEASE_LOST" in caplog.text) is expire


def test_failed_batch_is_visible_with_stable_error_code(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)

    def fail(**kwargs: object) -> dict[str, int]:
        raise RuntimeError("A private provider message must not be persisted")

    coordinator._runner.run_batch = fail  # type: ignore[method-assign]
    try:
        coordinator.run_sync()
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected the synthetic batch failure")

    batch = coordinator.status()["batch"]
    assert batch["last_error"]["code"] == "BATCH_FAILED"
    assert "private provider message" not in batch["last_error"]["detail"]
