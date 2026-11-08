"""API integration contracts for search cards and browser audio declarations."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from msj.api import create_app
from msj.auth import create_token
from msj.config import TASKS, Settings, TaskRoute


@pytest.mark.parametrize("tiers", ["1", "2", "3", "4", "5"])
def test_search_results_embed_summary_entry(tmp_path: Path, tiers: str) -> None:
    app = create_app(Settings(
        data_dir=tmp_path, embedding={"provider": "fake", "min_score": -1},
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    ))
    token = create_token(app.state.store)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as http:
        created = http.post(
            "/api/v1/entries", headers=headers, json={"text": "Atlas launch", "tags": ["work"]}
        )
        assert created.status_code == 201
        from msj.batch import BatchCoordinator
        from msj.pipeline import Runner

        BatchCoordinator(
            app.state.store,
            Runner(app.state.store, app.state.settings, embedding=app.state.embedding),
        ).run_sync()
        expected = http.get("/api/v1/entries", headers=headers).json()["items"][0]
        response = http.get(
            "/api/v1/search", headers=headers, params={"q": "Atlas", "tiers": tiers}
        )
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines()]
        results = [result for event in events for result in event.get("results", [])]
        assert results
        assert all(result["entry"] == expected for result in results)


@pytest.mark.parametrize("declared_type", ["audio/webm;codecs=opus", "audio/mp4;codecs=mp4a.40.2"])
def test_audio_preserves_full_declared_type(tmp_path: Path, declared_type: str) -> None:
    app = create_app(Settings(data_dir=tmp_path, embedding={"provider": "none"}))
    token = create_token(app.state.store)
    with TestClient(app) as http:
        response = http.post(
            "/api/v1/entries",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("recording", b"audio", declared_type)},
        )
        assert response.status_code == 201
        entry = http.get(
            f"/api/v1/entries/{response.json()['id']}",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        audio = next(artifact for artifact in entry["artifacts"] if artifact["kind"] == "raw_audio")
        assert audio["content_type"] == declared_type
