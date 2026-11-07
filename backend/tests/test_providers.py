"""Provider contract tests use only in-memory HTTP transports."""

import base64
import json
from typing import Any

import httpx
import pytest

from msj.config import Settings, TaskRoute
from msj.providers import Generation, InferenceProvider, ProviderError, ensure_model


def settings(**overrides: Any) -> Settings:
    return Settings(**overrides)


def test_runpod_llm_wire_format_accepts_list_wrapped_body(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/run"):
            return httpx.Response(
                200,
                json={
                    "id": "job-1",
                    "status": "COMPLETED",
                    "output": [
                        {
                            "body": {
                                "model": "Phi-4-mini",
                                "choices": [
                                    {"message": {"content": "clean"}},
                                ],
                            }
                        },
                    ],
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setenv("RUNPOD_API_KEY", "secret")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = InferenceProvider(settings(), http)
    route = TaskRoute(
        provider="runpod", endpoint_id="endpoint", model="any", expect_model="Phi-4-mini"
    )
    generated = provider.generate("cleanup", route, "raw")
    assert generated == Generation("clean", "Phi-4-mini")
    request = requests[0]
    assert request.headers["authorization"] == "Bearer secret"
    payload = json.loads(request.content)["input"]
    assert payload["openai_route"] == "/v1/chat/completions"
    body = payload["openai_input"]
    assert body["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert body["messages"][1]["content"] == '{"source": "raw"}'
    http.close()


def test_runpod_transcribe_sends_base64_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/run")
        payload = json.loads(request.content)["input"]
        assert payload == {"audio_base64": base64.b64encode(b"audio").decode(), "model": "turbo"}
        return httpx.Response(
            200,
            json={
                "id": "job",
                "status": "COMPLETED",
                "output": {"text": "transcript", "model": "turbo"},
            },
        )

    monkeypatch.setenv("RUNPOD_API_KEY", "secret")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = InferenceProvider(settings(), http)
    assert provider.generate(
        "transcribe",
        TaskRoute(provider="runpod", endpoint_id="ep", model="turbo"),
        "",
        audio=b"audio",
    ) == Generation("transcript", "turbo")
    http.close()


@pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT"])
def test_runpod_terminal_failure_is_actionable(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "job", "status": status})

    monkeypatch.setenv("RUNPOD_API_KEY", "secret")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = InferenceProvider(settings(), http, openai_transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match=f"status {status}"):
        provider.generate("cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "raw")
    http.close()


def test_openai_chat_wire_format_and_reported_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://local/v1/chat/completions"
        body = json.loads(request.content)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "extra_body" not in body
        assert body["model"] == "Phi-4-mini"
        return httpx.Response(
            200,
            json={
                "model": "Phi-4-mini",
                "choices": [
                    {"message": {"content": "output"}},
                ],
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = InferenceProvider(settings(), http, openai_transport=httpx.MockTransport(handler))
    route = TaskRoute(
        provider="openai", base_url="http://local/v1", model="Phi-4-mini", expect_model="Phi-4-mini"
    )
    assert provider.generate("cleanup", route, "source") == Generation("output", "Phi-4-mini")
    http.close()


def test_openai_transcription_uses_multipart() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b'name="model"' in request.content and b"turbo" in request.content
        assert b'name="file"' in request.content and b"audio-bytes" in request.content
        return httpx.Response(200, json={"text": "spoken text", "model": "turbo"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = InferenceProvider(settings(), http, openai_transport=httpx.MockTransport(handler))
    assert provider.generate(
        "transcribe",
        TaskRoute(provider="openai", base_url="http://local/v1", model="turbo"),
        "",
        audio=b"audio-bytes",
    ) == Generation("spoken text", "turbo")
    http.close()


def test_wrong_or_missing_model_is_rejected() -> None:
    route = TaskRoute(model="expected", expect_model="expected")
    for generation in (Generation("text", "other"), Generation("text", None)):
        with pytest.raises(ProviderError, match="Stale worker or wrong model"):
            ensure_model(generation, route)


def test_fake_extraction_is_valid_json() -> None:
    provider = InferenceProvider(
        settings(), httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    )
    result = provider.generate("extract", TaskRoute(model="fake"), "entry")
    assert json.loads(result.text)["projects"] == []


def test_runpod_timeout_requests_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    from msj import providers

    now = [0.0]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/run"):
            return httpx.Response(200, json={"id": "job", "status": "IN_PROGRESS"})
        if "/status/" in request.url.path:
            return httpx.Response(200, json={"id": "job", "status": "IN_PROGRESS"})
        if "/cancel/" in request.url.path:
            return httpx.Response(200, json={"id": "job", "status": "CANCELLED"})
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setenv("RUNPOD_API_KEY", "secret")
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        providers.time, "sleep", lambda duration: now.__setitem__(0, now[0] + duration)
    )
    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = InferenceProvider(settings(provider_timeout_s=61, lease_s=100), http)
    with pytest.raises(ProviderError, match="timed out; cancellation requested"):
        provider.generate("cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "raw")
    assert any("/cancel/" in path for path in calls)
    http.close()


@pytest.mark.parametrize("status", [[], {}, None])
def test_malformed_runpod_status_is_a_provider_failure(
    status: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"id": "job", "status": status})

    monkeypatch.setenv("RUNPOD_API_KEY", "test-only")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError, match="non-string job status"):
            InferenceProvider(
                settings(), http, openai_transport=httpx.MockTransport(handler)
            ).generate("cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "source")
    assert calls[-1].endswith("/cancel/job")


def test_truncated_chat_output_is_not_published() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "local",
                "choices": [{"message": {"content": "incomplete"}, "finish_reason": "length"}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError, match="increase max_tokens"):
            InferenceProvider(
                settings(), http, openai_transport=httpx.MockTransport(handler)
            ).generate("summarize", TaskRoute(provider="openai"), "source")
