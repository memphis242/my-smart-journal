"""Batch failure isolation exercised through the authenticated API."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from msj.api import create_app
from msj.auth import create_token
from msj.config import TASKS, Settings, TaskKind, TaskRoute
from msj.providers import Generation, InferenceProvider


class UnexpectedFailureProvider:
    def __init__(self, delegate: InferenceProvider, failure: Exception) -> None:
        self.delegate = delegate
        self.failure = failure

    def generate(
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        *,
        candidate: str | None = None,
        audio: bytes | None = None,
    ) -> Generation:
        if source.startswith("private"):
            raise self.failure
        return self.delegate.generate(kind, route, source, candidate=candidate, audio=audio)


@pytest.fixture
def batch_client(tmp_path: Path) -> Iterator[tuple[TestClient, dict[str, str], Settings]]:
    settings = Settings(
        data_dir=tmp_path, tasks={kind: TaskRoute(provider="fake") for kind in TASKS}
    )
    app = create_app(settings)
    token = create_token(app.state.store)
    with TestClient(app) as client:
        yield client, {"Authorization": "Bearer " + token}, settings


@pytest.mark.parametrize("exception_type", [RuntimeError, TypeError, ValueError])
def test_unexpected_job_errors_do_not_abort_batch_or_disclose_text(
    batch_client: tuple[TestClient, dict[str, str], Settings],
    exception_type: type[Exception],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, headers, settings = batch_client
    private = "private journal text must never appear in errors or logs"
    first = client.post("/api/v1/entries", headers=headers, json={"text": private}).json()
    healthy = client.post(
        "/api/v1/entries", headers=headers, json={"text": "I completed an ordinary task."}
    ).json()
    with httpx.Client() as http:
        delegate = InferenceProvider(settings, http)
        client.app.state.runner._provider = UnexpectedFailureProvider(
            delegate, exception_type(private)
        )
        response = client.post("/api/v1/batches/run", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"done": 6, "failed": 1, "discarded": 0}
    jobs: list[dict[str, Any]] = client.get("/api/v1/jobs", headers=headers).json()
    failed = next(
        job for job in jobs if job["entry_id"] == first["id"] and job["kind"] == "cleanup"
    )
    assert failed["status"] == "queued"
    assert "retry" in failed["last_error"].lower()
    assert private not in failed["last_error"]
    assert not any(job["status"] == "running" for job in jobs)
    assert all(job["status"] == "done" for job in jobs if job["entry_id"] == healthy["id"])
    assert exception_type.__name__ in caplog.text
    assert private not in caplog.text
    assert "Traceback" not in caplog.text


def test_oversized_runpod_audio_fails_once_without_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = {kind: TaskRoute(provider="fake") for kind in TASKS}
    routes["transcribe"] = TaskRoute(provider="runpod", endpoint_id="test-endpoint", model="turbo")
    settings = Settings(data_dir=tmp_path, tasks=routes, runpod_max_payload_bytes=128)
    app = create_app(settings)
    headers = {"Authorization": "Bearer " + create_token(app.state.store)}
    monkeypatch.setenv("RUNPOD_API_KEY", "mock-key-unused-by-network")
    requests: list[httpx.Request] = []

    def reject_submission(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("An oversized RunPod request must not be submitted")

    with httpx.Client(transport=httpx.MockTransport(reject_submission)) as http:
        app.state.runner._provider = InferenceProvider(settings, http)
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/entries",
                headers=headers,
                files={"file": ("voice.wav", b"audio" * 64, "audio/wav")},
            )
            assert response.status_code == 201
            assert client.post("/api/v1/batches/run", headers=headers).json()["failed"] == 1
            jobs = client.get("/api/v1/jobs", headers=headers).json()
            job = next(job for job in jobs if job["kind"] == "transcribe")
            assert job["status"] == "failed"
            assert job["attempts"] == 1
            assert "Opus/compressed" in job["last_error"]
            failed_id = job["id"]
            children = [dependent for dependent in jobs if dependent["id"] != failed_id]
            assert all(dependent["blocked_by"][0]["kind"] == "transcribe" for dependent in children)
            entry = client.get("/api/v1/entries/" + response.json()["id"], headers=headers).json()
            assert all(
                dependent["blocked_by"][0]["job_id"] == failed_id
                for dependent in entry["jobs"]
                if dependent["id"] != failed_id
            )
            assert client.post("/api/v1/batches/run", headers=headers).json()["failed"] == 0
            jobs = client.get("/api/v1/jobs", headers=headers).json()
            assert next(job for job in jobs if job["id"] == failed_id)["attempts"] == 1
    assert requests == []


def test_revising_a_failed_entry_supersedes_its_terminal_failure(tmp_path: Path) -> None:
    routes = {kind: TaskRoute(provider="fake") for kind in TASKS}
    routes["cleanup"] = TaskRoute(provider="fake", expect_model="real-model")
    app = create_app(Settings(data_dir=tmp_path, tasks=routes, max_attempts=1))
    headers = {"Authorization": "Bearer " + create_token(app.state.store)}
    with TestClient(app) as client:
        created = client.post("/api/v1/entries", headers=headers, json={"text": "First version"})
        entry_id = created.json()["id"]
        assert client.post("/api/v1/batches/run", headers=headers).json()["failed"] == 1
        failed = client.get("/api/v1/jobs?status=failed", headers=headers).json()
        assert len(failed) == 1
        revised = client.patch(
            "/api/v1/entries/" + entry_id, headers=headers, json={"text": "Revised version"}
        )
        assert revised.status_code == 200
        assert client.get("/api/v1/jobs?status=failed", headers=headers).json() == []
        superseded = client.get("/api/v1/jobs?status=superseded", headers=headers).json()
        assert failed[0]["id"] in {job["id"] for job in superseded}
