"""Provider review session messages use the complete producer conversation."""

from __future__ import annotations

import json
from typing import Any

import httpx

from msj.config import Settings, TaskKind, TaskRoute
from msj.providers import InferenceProvider, Message, revision_messages


def _messages() -> tuple[Message, ...]:
    return revision_messages(
        "extract",
        "original source",
        '{"projects": []}',
        ["candidate claim"],
    )


def test_revision_messages_preserve_source_output_and_claims() -> None:
    messages = _messages()
    assert len(messages) == 4
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == '{"source": "original source"}'
    assert messages[2]["role"] == "assistant"
    assert messages[2]["content"] == '{"projects": []}'
    revision = json.loads(messages[3]["content"])
    assert revision["unsupported_claims"] == ["candidate claim"]
    assert "valid against the original extraction schema" in revision["revision_request"]


def test_runpod_sends_all_revision_messages(monkeypatch: Any) -> None:
    messages = _messages()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)["input"]["openai_input"]
        assert body["messages"] == list(messages)
        assert body["model"] == "gemma-revision"
        assert body["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
        assert body["response_format"]["json_schema"]["schema"]["required"]
        return httpx.Response(
            200,
            json={
                "id": "run-1",
                "status": "COMPLETED",
                "output": {"model": "gemma-revision", "choices": [{"message": {"content": "{}"}}]},
            },
        )

    monkeypatch.setenv("RUNPOD_API_KEY", "mock-only")
    route = TaskRoute(provider="runpod", endpoint_id="test", model="gemma-revision")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = InferenceProvider(Settings(), http).generate(
            "extract", route, "original source", messages=messages
        )
    assert result.text == "{}"


def test_openai_sends_all_revision_messages() -> None:
    messages = _messages()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["messages"] == list(messages)
        assert body["model"] == "gemma-revision"
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["response_format"]["json_schema"]["schema"]["required"]
        return httpx.Response(
            200,
            json={"model": "gemma-revision", "choices": [{"message": {"content": "{}"}}]},
        )

    route = TaskRoute(provider="openai", base_url="http://local/v1", model="gemma-revision")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = InferenceProvider(
            Settings(), http, openai_transport=httpx.MockTransport(handler)
        ).generate("extract", route, "original source", messages=messages)
    assert result.text == "{}"


def test_artifact_hash_matches_prompt_used_before_provider_returns(
    tmp_path: Any, monkeypatch: Any
) -> None:
    import hashlib
    from importlib.resources import files

    from msj import pipeline
    from msj.config import TASKS
    from msj.pipeline import Runner
    from msj.providers import Generation, initial_messages
    from msj.storage import Store

    changed = tmp_path / "updated-review.txt"
    _ = changed.write_text("Updated prompt after submission")
    original_files = files

    class UpdatedResources:
        def joinpath(self, path: str) -> Any:
            return (
                changed if path == "resources/review.txt" else original_files("msj").joinpath(path)
            )

    settings = Settings(
        data_dir=tmp_path / "data", tasks={kind: TaskRoute(provider="fake") for kind in TASKS}
    )
    store = Store(settings)
    store.migrate()
    entry = store.create_entry(text="Journal source")
    expected: list[str] = []
    with httpx.Client() as http:
        delegate = InferenceProvider(settings, http)

        class PromptRecordingFake:
            def generate(
                self,
                kind: TaskKind,
                route: TaskRoute,
                source: str,
                *,
                candidate: str | None = None,
                audio: bytes | None = None,
            ) -> Generation:
                if kind == "review":
                    submitted = initial_messages(kind, source, candidate=candidate)
                    expected.append(hashlib.sha256(submitted[0]["content"].encode()).hexdigest())
                    monkeypatch.setattr(pipeline, "files", lambda _: UpdatedResources())
                return delegate.generate(kind, route, source, candidate=candidate, audio=audio)

        assert Runner(store, settings, PromptRecordingFake()).run_batch()["failed"] == 0  # type: ignore[arg-type]
    reviews = [
        artifact
        for artifact in store.get_entry(entry["id"], history=True)["artifacts"]
        if artifact["kind"] == "review"
    ]
    assert [review["prompt_sha256"] for review in reviews] == expected
