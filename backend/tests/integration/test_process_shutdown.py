"""SIGTERM while a job is in flight: cancel it, requeue it, release the lease, exit.

`cleanup` is routed to the held loopback chat server, which never answers on its own, so
the worker is inside a slow inference call when systemd-style SIGTERM arrives.
"""

import time
from typing import Any, Final

import pytest

from .msj_harness import CLEANED, STARTUP_TIMEOUT_S, HeldChatServer, MsjFactory, MsjInstance

pytestmark = pytest.mark.integration

# systemd's TimeoutStopSec is 30 s in the deployment units; the worker must be well inside it.
SHUTDOWN_BUDGET_S: Final = 20.0


def _cleanup_job(msj: MsjInstance, entry_id: str) -> dict[str, Any]:
    jobs = [job for job in msj.jobs() if job["entry_id"] == entry_id and job["kind"] == "cleanup"]
    assert len(jobs) == 1, jobs
    return jobs[0]


def test_sigterm_mid_job_requeues_it_and_releases_the_lease(
    msj_factory: MsjFactory, chat_server: HeldChatServer
) -> None:
    msj = msj_factory(
        {
            "provider_timeout_s": 60,
            "lease_s": 120,
            "tasks": {
                "cleanup": {
                    "provider": "openai",
                    "model": "fake-openai",
                    "base_url": chat_server.base_url,
                }
            },
        }
    ).boot()
    worker = msj.processes[-1]
    entry = msj.create_text_entry("rebuilt atlas index before the maintenance window")
    msj.force_batch()
    if not chat_server.wait_for_requests(1, STARTUP_TIMEOUT_S):
        pytest.fail(f"worker never called the chat server\n{msj.diagnostics()}")
    running = _cleanup_job(msj, entry["id"])
    assert running["status"] == "running", running
    assert running["attempts"] == 1, running

    started = time.monotonic()
    returncode = worker.terminate_and_wait(SHUTDOWN_BUDGET_S)
    elapsed = time.monotonic() - started
    assert returncode == 0, worker.logs()
    assert elapsed < SHUTDOWN_BUDGET_S, worker.logs()
    assert "SIGTERM received" in worker.output()

    requeued = _cleanup_job(msj, entry["id"])
    assert requeued["status"] == "queued", requeued
    assert requeued["attempts"] == 0, "an interrupted job must not use an attempt"
    assert "shutdown" in requeued["last_error"], requeued
    queue = msj.queue()
    assert queue["batch"]["running"] is False, queue
    assert queue["batch"]["last_error"] is None, queue
    assert queue["counts"]["running"] == 0, queue
    assert queue["counts"]["failed"] == 0, queue

    # The next worker retries at once and publishes exactly one cleanup result.
    chat_server.release()
    _ = msj.start_worker()
    msj.process_entries([entry["id"]])
    history = msj.entry(entry["id"], history=True)
    cleaned = [a for a in history["artifacts"] if a["kind"] == "cleaned"]
    assert len(cleaned) == 1, f"duplicate cleanup output: {cleaned}"
    assert cleaned[0]["content"] == CLEANED
    done = _cleanup_job(msj, entry["id"])
    assert done["status"] == "done", done
    assert done["attempts"] == 1, done
    assert len(chat_server.requests()) == 2
