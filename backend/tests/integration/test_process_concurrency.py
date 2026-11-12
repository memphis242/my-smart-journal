"""Concurrent API clients, and the API and worker writing to one SQLite database at once."""

import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final

import httpx
import pytest

from .msj_harness import PROCESSING_TIMEOUT_S, MsjFactory, MsjInstance, wait_until

pytestmark = pytest.mark.integration

CLIENTS: Final = 8
# Bounded so the follow-up processing stays small however long the overlapped batch runs.
CREATES_PER_WRITER: Final = 5
TEXT_REVISIONS_PER_WRITER: Final = 3


def _concurrent_patches(
    msj: MsjInstance, entry_id: str, bodies: list[dict[str, Any]]
) -> list[httpx.Response]:
    """Send each body from its own client, released together by a barrier."""
    clients = [msj.client() for _ in bodies]
    barrier = threading.Barrier(len(bodies))

    def send(index: int) -> httpx.Response:
        _ = barrier.wait(timeout=30)
        return clients[index].patch(f"/api/v1/entries/{entry_id}", json=bodies[index])

    with ThreadPoolExecutor(max_workers=len(bodies)) as pool:
        return list(pool.map(send, range(len(bodies))))


def test_concurrent_patches_respect_expected_version(msj_factory: MsjFactory) -> None:
    msj = msj_factory().boot(worker=False)
    entry = msj.create_text_entry("Version race target.")
    initial = int(entry["version"])

    responses = _concurrent_patches(
        msj,
        entry["id"],
        [{"title": f"writer {index}", "expected_version": initial} for index in range(CLIENTS)],
    )
    statuses = Counter(response.status_code for response in responses)
    assert statuses == {200: 1, 409: CLIENTS - 1}, [(r.status_code, r.text) for r in responses]
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert winner["version"] == initial + 1
    for response in responses:
        if response.status_code == 409:
            problem = msj.expect_problem(response, 409, "ENTRY_CHANGED")
            assert problem["current_version"] == initial + 1
    current = msj.entry(entry["id"])
    assert current["version"] == initial + 1
    assert current["title"] == winner["title"]

    responses = _concurrent_patches(
        msj, entry["id"], [{"tags": [f"tag-{index}"]} for index in range(CLIENTS)]
    )
    assert [response.status_code for response in responses] == [200] * CLIENTS, [
        response.text for response in responses
    ]
    versions = sorted(int(response.json()["version"]) for response in responses)
    assert versions == list(range(initial + 2, initial + 2 + CLIENTS)), versions
    assert msj.entry(entry["id"])["version"] == initial + 1 + CLIENTS


class _Writers:
    """API writer threads that create and patch entries until told to stop.

    Title patches continue for as long as the writers run; entry creation and text
    revisions (which queue new pipelines) stop after a fixed count per thread.
    """

    def __init__(self, msj: MsjInstance, seeded: list[str], threads: int) -> None:
        self._seeded = seeded
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.created: list[str] = []
        self.patches: Counter[str] = Counter()
        self.text_patched: set[str] = set()
        self.failures: list[str] = []
        self.operations = 0
        self._threads = [
            threading.Thread(target=self._run, args=(index, msj.client()), name=f"writer-{index}")
            for index in range(threads)
        ]

    def start(self) -> None:
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=60)
            assert not thread.is_alive(), f"{thread.name} did not stop"

    def _record(self, label: str, response: httpx.Response, expected: int) -> bool:
        with self._lock:
            self.operations += 1
            if response.status_code != expected:
                self.failures.append(f"{label}: {response.status_code} {response.text[:300]}")
                return False
        return True

    def _run(self, index: int, client: httpx.Client) -> None:
        round_number = 0
        while not self._stop.is_set():
            round_number += 1
            target = self._seeded[(index + round_number) % len(self._seeded)]
            if round_number % 4 == 1 and round_number // 4 < CREATES_PER_WRITER:
                response = client.post(
                    "/api/v1/entries",
                    json={"text": f"Writer {index} round {round_number} notes on Atlas."},
                )
                if self._record("create", response, 201):
                    with self._lock:
                        self.created.append(str(response.json()["id"]))
            response = client.patch(
                f"/api/v1/entries/{target}", json={"title": f"w{index} r{round_number}"}
            )
            if self._record("patch title", response, 200):
                with self._lock:
                    self.patches[target] += 1
            if round_number % 6 == 0 and round_number // 6 <= TEXT_REVISIONS_PER_WRITER:
                response = client.patch(
                    f"/api/v1/entries/{target}",
                    json={"text": f"Revised by writer {index} in round {round_number}."},
                )
                if self._record("patch text", response, 200):
                    with self._lock:
                        self.patches[target] += 1
                        self.text_patched.add(target)
            _ = self._stop.wait(0.02)


def test_api_and_worker_write_concurrently(msj: MsjInstance) -> None:
    seeded = [
        msj.create_text_entry(f"Seed entry {index} about Atlas.")["id"] for index in range(24)
    ]
    before = msj.queue()["batch"]["last_finished_at"]

    writers = _Writers(msj, seeded, threads=4)
    writers.start()
    try:
        msj.force_batch()
        # Writers stay active for the whole batch: from before the request until it finishes.
        _ = wait_until(
            lambda: True if msj.queue()["batch"]["last_finished_at"] != before else None,
            timeout=PROCESSING_TIMEOUT_S,
            describe=lambda: f"the forced batch to finish\n{msj.diagnostics()}",
            interval=0.1,
        )
        time.sleep(0.2)
    finally:
        writers.stop()

    assert not writers.failures, "\n".join(writers.failures[:20]) + "\n" + msj.diagnostics()
    assert writers.operations > 0
    finished = msj.queue()["batch"]
    assert finished["last_error"] is None, finished
    assert finished["last_result"]["processed"] > 0, finished

    all_ids = seeded + writers.created
    msj.process_entries(all_ids, timeout=PROCESSING_TIMEOUT_S * 2)

    listing = msj.expect(msj.api.get("/api/v1/entries", params={"limit": 500}), 200)
    assert listing["total"] == len(all_ids)
    assert {item["id"] for item in listing["items"]} == set(all_ids)
    assert {item["status"] for item in listing["items"]} == {"done"}
    for entry_id in seeded:
        entry = msj.entry(entry_id)
        assert entry["version"] == 1 + writers.patches[entry_id], (entry_id, entry["version"])
        expected_revision_floor = 2 if entry_id in writers.text_patched else 1
        assert entry["revision"] >= expected_revision_floor
        current = Counter(a["kind"] for a in entry["artifacts"])
        assert current["cleaned"] == 1, (entry_id, current)
        assert current["summary"] == 1, (entry_id, current)

    assert msj.expect(msj.api.get("/api/v1/jobs", params={"status": "failed"}), 200) == []
    queue = msj.queue()
    assert queue["counts"] == {"queued": 0, "running": 0, "failed": 0, "blocked": 0}, queue
    output = msj.all_process_output().lower()
    assert "database is locked" not in output, msj.diagnostics()
    assert "traceback" not in output, msj.diagnostics()
