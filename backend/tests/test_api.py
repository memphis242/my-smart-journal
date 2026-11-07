"""End-to-end API behavior with SQLite and deterministic fake inference."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from msj.api import create_app
from msj.auth import create_token
from msj.config import TASKS, Settings, TaskRoute


@pytest.fixture
def client(tmp_path: Path) -> tuple[TestClient, str]:
    app = create_app(
        Settings(
            data_dir=tmp_path,
            upload_limit_bytes=4,
            tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
        )
    )
    token = create_token(app.state.store)
    with TestClient(app) as test_client:
        yield test_client, token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_text_entry_batch_artifacts_and_search(client: tuple[TestClient, str]) -> None:
    http, token = client
    unauthorized = http.get("/api/v1/entries")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["content-type"].startswith("application/problem+json")

    response = http.post(
        "/api/v1/entries",
        headers=_headers(token),
        json={"text": "I finished Atlas migration on Friday."},
    )
    assert response.status_code == 201
    entry_id = response.json()["id"]
    batch = http.post("/api/v1/batches/run", headers=_headers(token))
    assert batch.status_code == 200
    assert batch.json() == {"done": 4, "failed": 0, "discarded": 0}

    entry = http.get(f"/api/v1/entries/{entry_id}", headers=_headers(token)).json()
    artifacts = {item["kind"]: item for item in entry["artifacts"]}
    assert {"user_text", "cleaned", "summary", "extraction", "review"} <= set(artifacts)
    assert json.loads(artifacts["extraction"]["content"])["projects"] == []
    search = http.get("/api/v1/search", params={"q": "Atlas migration"}, headers=_headers(token))
    assert search.status_code == 200
    events: list[dict[str, Any]] = [json.loads(line) for line in search.text.splitlines()]
    assert [event.get("tier") for event in events[:-1]] == [1, 2, 3, 4]
    assert events[0]["results"][0]["entry_id"] == entry_id
    assert events[-1]["done"] is True


def test_audio_upload_validation_and_size_limit(client: tuple[TestClient, str]) -> None:
    http, token = client
    headers = _headers(token)
    accepted = http.post(
        "/api/v1/entries", headers=headers, files={"file": ("voice.wav", b"1234", "audio/wav")}
    )
    assert accepted.status_code == 201
    assert accepted.json()["source"] == "audio"
    rejected_type = http.post(
        "/api/v1/entries", headers=headers, files={"file": ("voice.txt", b"x", "text/plain")}
    )
    assert rejected_type.status_code == 415
    too_large = http.post(
        "/api/v1/entries", headers=headers, files={"file": ("voice.wav", b"12345", "audio/wav")}
    )
    assert too_large.status_code == 413


def test_patch_validation_history_delete_and_health(client: tuple[TestClient, str]) -> None:
    http, token = client
    headers = _headers(token)
    assert http.get("/api/v1/health").json() == {"status": "ok"}
    created = http.post(
        "/api/v1/entries", headers=headers, json={"text": "First version", "tags": ["work"]}
    ).json()
    entry_id = created["id"]
    invalid = http.patch(f"/api/v1/entries/{entry_id}", headers=headers, json={"tags": "work"})
    assert invalid.status_code == 422
    revised = http.patch(
        f"/api/v1/entries/{entry_id}",
        headers=headers,
        json={"text": "Second version", "tags": ["work", "journal"]},
    )
    assert revised.status_code == 200
    history = http.get(f"/api/v1/entries/{entry_id}?history=1", headers=headers).json()
    texts = [
        artifact["content"] for artifact in history["artifacts"] if artifact["kind"] == "user_text"
    ]
    assert texts == ["First version", "Second version"]
    deleted = http.delete(f"/api/v1/entries/{entry_id}", headers=headers)
    assert deleted.status_code == 204
    assert http.get(f"/api/v1/entries/{entry_id}", headers=headers).status_code == 404


def test_token_storage_keeps_only_a_salted_hash(client: tuple[TestClient, str]) -> None:
    http, token = client
    stored_hash = http.app.state.store.token_hash()
    assert stored_hash is not None
    assert token not in stored_hash
    assert stored_hash.startswith("scrypt$")
    assert http.get("/api/v1/jobs", headers=_headers("invalid-token")).status_code == 401


def test_failed_jobs_filter_and_manual_retry(tmp_path: Path) -> None:
    routes = {kind: TaskRoute(provider="fake") for kind in TASKS}
    routes["cleanup"] = TaskRoute(provider="fake", expect_model="expected-real-model")
    app = create_app(Settings(data_dir=tmp_path, tasks=routes, max_attempts=1))
    token = create_token(app.state.store)
    with TestClient(app) as http:
        headers = _headers(token)
        created = http.post("/api/v1/entries", headers=headers, json={"text": "Planned a release."})
        assert created.status_code == 201
        assert http.post("/api/v1/batches/run", headers=headers).json()["failed"] == 1
        failed = http.get("/api/v1/jobs", params={"status": "failed"}, headers=headers)
        assert failed.status_code == 200
        job = failed.json()[0]
        assert job["kind"] == "cleanup"
        assert "wrong model" in job["last_error"]
        retried = http.post(f"/api/v1/jobs/{job['id']}/retry", headers=headers)
        assert retried.status_code == 200
        assert retried.json()["status"] == "queued"
        assert retried.json()["attempts"] == 0
        assert (
            http.get("/api/v1/jobs", params={"status": "unknown"}, headers=headers).status_code
            == 422
        )
