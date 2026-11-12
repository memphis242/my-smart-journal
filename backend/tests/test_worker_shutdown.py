"""A stop request cancels in-flight inference, requeues the job and releases the lease."""

import asyncio
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import httpx
import pytest

from msj.batch import BatchCoordinator, queue_status
from msj.config import TASKS, Settings, TaskKind, TaskRoute
from msj.pipeline import Runner
from msj.providers import Generation, InferenceProvider, Message
from msj.shutdown import ShutdownRequested
from msj.storage import Store


@pytest.fixture
def stop_soon() -> Iterator[threading.Event]:
    """A stop event set shortly after the test starts its in-flight request."""
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    yield stop
    timer.cancel()


def test_stop_request_ends_batch_after_current_job_and_releases_lease(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        embedding={"provider": "none"},
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    for text in ("First synthetic entry.", "Second synthetic entry."):
        _ = store.create_entry(text=text)
    stop = threading.Event()
    runner = Runner(store, settings, stop=stop)
    original_finish = store.finish_job
    finished: list[str] = []

    def finish_then_request_stop(job: dict[str, object], *args: object, **kwargs: object) -> bool:
        published = original_finish(job, *args, **kwargs)  # type: ignore[arg-type]
        finished.append(str(job["id"]))
        stop.set()
        return published

    store.finish_job = finish_then_request_stop  # type: ignore[method-assign]
    result = BatchCoordinator(store, runner).run_sync("manual")

    assert result["done"] == 1
    assert len(finished) == 1
    status = queue_status(store)
    assert status["batch"]["running"] is False
    assert status["batch"]["last_error"] is None
    assert status["counts"]["running"] == 0
    assert status["counts"]["queued"] > 0

    # The next batch resumes the remaining work.
    stop.clear()
    store.finish_job = original_finish  # type: ignore[method-assign]
    resumed = BatchCoordinator(store, Runner(store, settings)).run_sync("manual")
    assert resumed["done"] > 0
    assert resumed["failed"] == 0


def test_runpod_job_is_cancelled_promptly_on_shutdown(
    monkeypatch: pytest.MonkeyPatch, stop_soon: threading.Event
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/run"):
            return httpx.Response(200, json={"id": "job", "status": "IN_QUEUE"})
        if "/status/" in request.url.path:
            return httpx.Response(200, json={"id": "job", "status": "IN_PROGRESS"})
        if "/cancel/" in request.url.path:
            return httpx.Response(200, json={"id": "job", "status": "CANCELLED"})
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setenv("RUNPOD_API_KEY", "synthetic")
    # Long poll intervals: only the stop request can end the wait early.
    settings = Settings(poll_initial_s=30, poll_max_s=30)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        provider = InferenceProvider(settings, http, stop=stop_soon)
        started = time.monotonic()
        with pytest.raises(ShutdownRequested):
            _ = provider.generate("cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "x")
    assert time.monotonic() - started < 5
    assert calls[-1].endswith("/cancel/job")


def test_openai_request_is_abandoned_promptly_on_shutdown(stop_soon: threading.Event) -> None:
    class StalledTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(3600)
            raise AssertionError("the stalled request was not cancelled")

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as http:
        provider = InferenceProvider(
            Settings(), http, openai_transport=StalledTransport(), stop=stop_soon
        )
        started = time.monotonic()
        with pytest.raises(ShutdownRequested):
            _ = provider.generate("cleanup", TaskRoute(provider="openai"), "x")
    assert time.monotonic() - started < 5


def test_interrupted_job_is_requeued_without_using_an_attempt(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        embedding={"provider": "none"},
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    entry = store.create_entry(text="Synthetic entry interrupted mid-cleanup.")
    stop = threading.Event()

    class InterruptedProvider:
        def generate(
            self,
            kind: TaskKind,
            route: TaskRoute,
            source: str,
            *,
            candidate: str | None = None,
            audio: bytes | None = None,
            messages: Sequence[Message] | None = None,
        ) -> Generation:
            stop.set()
            raise ShutdownRequested("synthetic shutdown")

    result = BatchCoordinator(
        store, Runner(store, settings, provider=InterruptedProvider(), stop=stop)
    ).run_sync("manual")

    assert result["failed"] == 0
    cleanup = next(job for job in store.list_jobs() if job["kind"] == "cleanup")
    assert cleanup["status"] == "queued"
    assert cleanup["attempts"] == 0
    assert "shutdown" in cleanup["last_error"]
    assert cleanup["not_before"] <= time.time()
    status = queue_status(store)
    assert status["batch"]["running"] is False
    assert status["batch"]["last_error"] is None

    resumed = BatchCoordinator(store, Runner(store, settings)).run_sync("manual")
    assert resumed["failed"] == 0
    assert store.get_entry(entry["id"])["status"] == "done"
