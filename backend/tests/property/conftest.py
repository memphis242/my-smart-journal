"""Shared in-process API fixture for property tests."""

from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fastapi.testclient import TestClient

from msj.api import create_app
from msj.auth import create_token
from msj.config import TASKS, EmbeddingSettings, ReviewSettings, Settings, TaskRoute


class Api(NamedTuple):
    client: TestClient
    headers: dict[str, str]
    entry_id: str


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Api]:
    """One app per test module; examples share it, so properties must not depend on emptiness."""
    data_dir: Path = tmp_path_factory.mktemp("property-api")
    settings = Settings(
        data_dir=data_dir,
        embedding=EmbeddingSettings(provider="fake"),
        review=ReviewSettings(enabled_for=()),
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
        login_rate_limit=1_000_000,
        bearer_rate_limit=1_000_000,
    )
    app = create_app(settings)
    headers = {"Authorization": "Bearer " + create_token(app.state.store)}
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/api/v1/entries", headers=headers, json={"text": "seed entry"})
    assert created.status_code == 201
    yield Api(client, headers, str(created.json()["id"]))
    client.close()
