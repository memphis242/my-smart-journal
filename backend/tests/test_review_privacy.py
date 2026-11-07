"""Private journal data must not enter diagnostic logs."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from msj.api import create_app
from msj.auth import create_token
from msj.config import TASKS, Settings, TaskRoute
from msj.pipeline import Runner
from msj.providers import Generation, ProviderError
from msj.storage import Store


@pytest.mark.parametrize("failure_stage", ["documents", "fts"])
def test_api_failures_log_only_exception_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_stage: str,
) -> None:
    settings = Settings(
        data_dir=tmp_path, tasks={kind: TaskRoute(provider="fake") for kind in TASKS}
    )
    app = create_app(settings)
    token = create_token(app.state.store)
    private = "private journal and model output must not appear in logs"

    def fail(*_args: object) -> None:
        raise RuntimeError(private)

    attribute = "search_documents" if failure_stage == "documents" else "fts_search"
    monkeypatch.setattr(app.state.store, attribute, fail)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/search?q=Atlas", headers={"Authorization": "Bearer " + token}
        )
    assert response.status_code == (500 if failure_stage == "documents" else 200)
    assert "RuntimeError" in caplog.text
    assert private not in caplog.text
    assert private not in response.text


def test_pluggable_provider_errors_cannot_publish_private_output(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        data_dir=tmp_path, tasks={kind: TaskRoute(provider="fake") for kind in TASKS}
    )
    store = Store(settings)
    store.migrate()
    private_source = "Private journal source describing a conversation"
    private_output = "Private model output quoting that conversation"

    class EchoingProvider:
        def generate(self, *_args: object, **_kwargs: object) -> Generation:
            raise ProviderError(private_source + " / " + private_output, retryable=False)

    _ = store.create_entry(text=private_source)
    result = Runner(store, settings, EchoingProvider()).run_batch()
    assert result["failed"] == 1
    failed = store.list_jobs(status="failed")[0]
    assert failed["attempts"] == 1
    assert "provider" in failed["last_error"].lower()
    assert private_source not in failed["last_error"]
    assert private_output not in failed["last_error"]
    assert private_source not in caplog.text
    assert private_output not in caplog.text
