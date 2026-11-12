"""A worker SIGKILLed mid-batch is recovered by the next worker exactly once.

`cleanup` is routed to the held loopback chat server (`HeldChatServer`), so the worker is
reliably inside a running job when it is killed.
"""

from typing import Any

import pytest

from .msj_harness import CLEANED, STARTUP_TIMEOUT_S, HeldChatServer, MsjFactory, MsjInstance

pytestmark = pytest.mark.integration


def _cleanup_job(msj: MsjInstance, entry_id: str) -> dict[str, Any]:
    jobs = [job for job in msj.jobs() if job["entry_id"] == entry_id and job["kind"] == "cleanup"]
    assert len(jobs) == 1, jobs
    return jobs[0]


def test_sigkilled_worker_job_is_reclaimed_and_completes_once(
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
    first_worker = msj.processes[-1]
    entry = msj.create_text_entry("rebuilt atlas index after the outage")
    msj.force_batch()

    if not chat_server.wait_for_requests(1, STARTUP_TIMEOUT_S):
        pytest.fail(f"worker never called the chat server\n{msj.diagnostics()}")
    request = chat_server.requests()[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["model"] == "fake-openai"
    running = _cleanup_job(msj, entry["id"])
    assert running["status"] == "running", running
    assert running["attempts"] == 1, running
    assert msj.queue()["batch"]["running"] is True

    first_worker.kill_hard()
    _ = msj.start_worker()
    # The new worker's start-up batch normally holds the lease already (409).
    _ = msj.request_batch()

    if not chat_server.wait_for_requests(2, STARTUP_TIMEOUT_S):
        pytest.fail(f"replacement worker never retried cleanup\n{msj.diagnostics()}")
    reclaimed = _cleanup_job(msj, entry["id"])
    assert reclaimed["status"] == "running", reclaimed
    assert reclaimed["attempts"] == 2, reclaimed
    assert reclaimed["last_error"], "a reclaimed job keeps a reclaim reason in last_error"
    lease = msj.queue()["batch"]
    assert lease["running"] is True
    assert lease["last_error"] is not None, lease
    assert lease["last_error"]["code"] == "BATCH_LEASE_LOST", lease

    chat_server.release()
    msj.process_entries([entry["id"]])

    history = msj.entry(entry["id"], history=True)
    cleaned = [a for a in history["artifacts"] if a["kind"] == "cleaned"]
    assert len(cleaned) == 1, f"duplicate cleanup output: {cleaned}"
    assert cleaned[0]["revision"] == 1
    assert cleaned[0]["content"] == CLEANED
    assert cleaned[0]["model"] == "fake-openai"
    for kind in ("summary", "extraction"):
        assert sum(1 for a in history["artifacts"] if a["kind"] == kind) == 1, kind

    done = _cleanup_job(msj, entry["id"])
    assert done["status"] == "done", done
    assert done["attempts"] == 2, done
    assert done["last_error"] is None, done
    assert all(job["status"] == "done" for job in history["jobs"]), history["jobs"]
    assert len(chat_server.requests()) == 2, "cleanup must not be called a third time"

    queue = msj.queue()
    assert queue["counts"]["running"] == 0, queue
    assert queue["counts"]["failed"] == 0, queue
    assert not [item for item in queue["items"] if item["status"] in {"running", "failed"}]
    assert queue["batch"]["running"] is False
