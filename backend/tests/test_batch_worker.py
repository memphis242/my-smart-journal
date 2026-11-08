from __future__ import annotations

import os
import socket
import sqlite3
import threading
from pathlib import Path

import pytest

from msj.batch import BatchCoordinator, _boot_id
from msj.config import TASKS, Settings, TaskRoute
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
            (10**12, socket.gethostname(), 2**30, _boot_id(), "old-process"),
        )

    assert coordinator.status()["batch"]["running"] is False
    result = coordinator.run_requested()

    assert result is not None
    assert coordinator.status()["batch"]["request_pending"] is False


def test_dead_local_lease_can_be_reclaimed(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    store = coordinator._store
    with store.connect() as db:
        _ = db.execute(
            "UPDATE batch_state SET batch_id='crashed',lease_until=?,owner_host=?,"
            "owner_pid=?,owner_boot_id=?,owner_started_at=? WHERE id=1",
            (10**12, socket.gethostname(), 2**30, _boot_id(), "2026-01-01T00:00:00+00:00"),
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

