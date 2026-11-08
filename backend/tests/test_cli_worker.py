"""Exercise the worker loop against persisted requests and batch leases."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from msj import cli
from msj.auth import create_token
from msj.batch import BatchCoordinator, queue_status
from msj.config import TASKS, Settings, TaskRoute
from msj.pipeline import Runner
from msj.storage import Store


def test_worker_conflict_rechecks_lease_and_picks_up_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path, worker_tick_s=0.25, embedding={"provider": "none"})
    store = Store(settings)
    store.migrate()
    other = BatchCoordinator(store, Runner(store, settings))
    held = other.acquire("manual")
    assert held is not None
    elapsed = [0.0]
    real_monotonic = cli.time.monotonic
    origin = real_monotonic()
    monkeypatch.setattr(cli.time, "monotonic", lambda: origin + elapsed[0])
    picked_up: list[float] = []
    original_run = BatchCoordinator.run_requested

    def run_requested(self: BatchCoordinator) -> dict[str, Any] | None:
        result = original_run(self)
        if result is not None:
            picked_up.append(elapsed[0])
        return result

    class Wake:
        def is_set(self) -> bool:
            return elapsed[0] >= 1.0

        def wait(self, delay: float) -> bool:
            assert delay > 0, "A conflicted worker must wait between attempts"
            if elapsed[0] == 0:
                other.release(held, {"done": 0, "failed": 0})
                assert other.request()
            elapsed[0] += delay
            return self.is_set()

    # Only replace the CLI wake event; renewal threads retain real threading events.
    class Threading:
        Event = Wake

    monkeypatch.setattr(cli, "threading", Threading)
    monkeypatch.setattr(BatchCoordinator, "run_requested", run_requested)
    cli._worker(settings)
    assert picked_up and picked_up[0] <= settings.worker_tick_s
    assert not queue_status(store)["batch"]["request_pending"]


def test_worker_publishes_schedule_instead_of_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path, worker_tick_s=0.25, embedding={"provider": "none"})
    store = Store(settings)
    store.migrate()
    waits = [0]

    class Wake:
        def is_set(self) -> bool:
            return waits[0] >= 2

        def wait(self, delay: float) -> bool:
            waits[0] += 1
            return self.is_set()

    class Threading:
        Event = Wake

    monkeypatch.setattr(cli, "threading", Threading)
    original_run = BatchCoordinator.run_sync

    def run_sync(self: BatchCoordinator, *args: Any, **kwargs: Any) -> dict[str, Any]:
        during = datetime.fromisoformat(queue_status(store)["worker"]["next_run_at"]).timestamp()
        assert during - cli.time.time() > settings.batch_interval_s - 2
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(BatchCoordinator, "run_sync", run_sync)
    before = cli.time.time()
    cli._worker(settings)
    next_at = datetime.fromisoformat(queue_status(store)["worker"]["next_run_at"]).timestamp()
    assert settings.batch_interval_s - 2 <= next_at - before <= settings.batch_interval_s + 2


@pytest.mark.parametrize("command", ["worker", "run-batch"])
@pytest.mark.parametrize("key_state", ["legacy", "missing", "mismatched"])
def test_processing_allows_token_reissue(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    command: str, key_state: str,
) -> None:
    settings = Settings(
        data_dir=tmp_path, embedding={"provider": "none"},
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    _ = create_token(store)
    match key_state:
        case "legacy":
            store.set_token_hash("reissue-required")
        case "missing":
            (tmp_path / "token.secret").unlink()
        case "mismatched":
            _ = (tmp_path / "token.secret").write_bytes(b"z" * 32)
    store.create_entry(text="process despite an expired credential")
    finished = [False]

    class Wake:
        def is_set(self) -> bool:
            return finished[0]

        def wait(self, delay: float) -> bool:
            finished[0] = True
            return True

    class Threading:
        Event = Wake

    monkeypatch.setattr(cli, "threading", Threading)
    match command:
        case "run-batch":
            result = cli._run_batch(settings)
            assert result["done"] > 0
        case "worker":
            cli._worker(settings)
            assert queue_status(store)["batch"]["last_result"]["processed"] > 0
    assert "TOKEN_REISSUE_REQUIRED" in caplog.text
    assert "msj create-token" in caplog.text


@pytest.mark.parametrize("interval_s", [0.1, 60.0])
def test_worker_publishes_earlier_schedule_or_conflict_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interval_s: float,
) -> None:
    settings = Settings(
        data_dir=tmp_path, worker_tick_s=0.25, batch_interval_s=interval_s,
        embedding={"provider": "none"},
    )
    store = Store(settings)
    store.migrate()
    other = BatchCoordinator(store, Runner(store, settings))
    held = other.acquire("manual")
    assert held is not None
    finished = [False]

    class Wake:
        def is_set(self) -> bool:
            return finished[0]

        def wait(self, delay: float) -> bool:
            published = datetime.fromisoformat(
                queue_status(store)["worker"]["next_run_at"]
            ).timestamp()
            expected = min(settings.worker_tick_s, interval_s)
            assert 0 < delay <= expected
            assert abs(published - cli.time.time() - delay) < 0.05
            finished[0] = True
            return True

    class Threading:
        Event = Wake

    monkeypatch.setattr(cli, "threading", Threading)
    cli._worker(settings)
    other.release(held, None)
