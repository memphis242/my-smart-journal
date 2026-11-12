"""User-visible failure reporting for embedding jobs and OpenAI-compatible providers."""

import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest

from msj.config import TASKS, EmbeddingSettings, Settings, TaskKind, TaskRoute
from msj.pipeline import Runner
from msj.providers import InferenceProvider, ProviderError
from msj.storage import Store


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (RuntimeError("private model detail"), "EMBEDDING_MODEL_LOAD_FAILED"),
        (ModuleNotFoundError("sentence_transformers"), "EMBEDDING_DEPENDENCY_MISSING"),
    ],
)
def test_failed_embedding_job_names_code_and_remedy_and_batch_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception, code: str
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        embedding=EmbeddingSettings(provider="sentence-transformers", model="test-model"),
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    entry = store.create_entry(text="Synthetic entry whose embedding model is unavailable.")

    def load_model(*_args: Any, **_kwargs: Any) -> Any:
        raise failure

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = load_model
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    result = Runner(store, settings).run_batch()

    assert result["failed"] == 1
    jobs = store.get_entry(entry["id"])["jobs"]
    embed = next(job for job in jobs if job["kind"] == "embed")
    assert embed["last_error"].startswith(code)
    assert "private model detail" not in embed["last_error"]
    assert all(job["status"] == "done" for job in jobs if job["kind"] != "embed")


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, outcome: httpx.Response | Exception) -> None:
        self._outcome = outcome

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


@pytest.mark.parametrize(
    ("kind", "outcome", "message", "transient"),
    [
        ("cleanup", httpx.ConnectError("refused"), "check connectivity", True),
        ("cleanup", httpx.ReadTimeout("slow"), "timed out", True),
        ("cleanup", httpx.Response(200, content=b"not json"), "invalid JSON", False),
        ("transcribe", httpx.Response(200, json={"words": []}), "text field", False),
        ("transcribe", httpx.Response(200, json={"text": "x", "model": 3}), "model", False),
    ],
)
def test_openai_failures_are_specific_and_safe(
    kind: TaskKind, outcome: httpx.Response | Exception, message: str, transient: bool
) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as http:
        provider = InferenceProvider(Settings(), http, openai_transport=_Transport(outcome))
        with pytest.raises(ProviderError, match=message) as failure:
            _ = provider.generate(
                kind,
                TaskRoute(provider="openai"),
                "audio/entry.wav",
                audio=b"RIFF" if kind == "transcribe" else None,
            )
    assert failure.value.transient is transient
