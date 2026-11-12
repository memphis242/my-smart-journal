"""Provider regressions for payload caps, polling and total request deadlines."""

import asyncio
import base64
import json
from typing import Any, cast

import httpx
import pytest

from msj.config import Settings, TaskKind, TaskRoute
from msj.providers import InferenceProvider, ProviderError


def settings(**changes: Any) -> Settings:
    return Settings(**changes)


def test_runpod_transcript_payload_limit_fails_before_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-only")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json={"id": "job", "status": "COMPLETED", "output": {"text": "x"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = InferenceProvider(settings(runpod_max_payload_bytes=20), client)
        with pytest.raises(ProviderError, match="audio too large for RunPod request") as raised:
            provider.generate(
                "transcribe",
                TaskRoute(provider="runpod", endpoint_id="ep", model="turbo"),
                "",
                audio=b"audio",
            )
    assert not raised.value.retryable
    assert calls == []


def test_runpod_payload_cap_matches_exact_encoded_request_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-only")
    audio = b"small audio"
    expected = json.dumps(
        {"input": {"audio_base64": base64.b64encode(audio).decode(), "model": "turbo"}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    actual: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        actual.append(request.content)
        return httpx.Response(
            200,
            json={"id": "job", "status": "COMPLETED", "output": {"text": "done", "model": "turbo"}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        InferenceProvider(settings(runpod_max_payload_bytes=len(expected)), client).generate(
            "transcribe",
            TaskRoute(provider="runpod", endpoint_id="ep", model="turbo"),
            "",
            audio=audio,
        )
    assert actual == [expected]


@pytest.mark.parametrize(
    ("provider", "kind"), [("runpod", "cleanup"), ("openai", "cleanup"), ("openai", "transcribe")]
)
def test_http_413_is_nonretryable_request_size_error(
    provider: str,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if provider == "runpod":
        monkeypatch.setenv("RUNPOD_API_KEY", "test-only")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = InferenceProvider(
            settings(), client, openai_transport=httpx.MockTransport(handler)
        )
        with pytest.raises(ProviderError, match="request too large") as raised:
            adapter.generate(
                cast(TaskKind, kind),
                TaskRoute(provider=provider, endpoint_id="ep", model="turbo"),
                "source",
                audio=b"audio" if kind == "transcribe" else None,
            )
    assert not raised.value.retryable


def test_runpod_paused_error_preserves_actionable_control_plane_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "secret-must-not-appear")
    body: dict[str, Any] = {
        "status": 409,
        "title": "Conflict",
        "detail": "Endpoint is paused (max_workers=0). Set max_workers > 0 to accept work.",
        "code": "ENDPOINT_PAUSED",
    }

    transport = httpx.MockTransport(lambda _: httpx.Response(409, json=body))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ProviderError) as raised:
            InferenceProvider(settings(), client).generate(
                "cleanup", TaskRoute(provider="runpod", endpoint_id="ep-17"), "private journal"
            )
    message = str(raised.value)
    assert "HTTP 409" in message
    assert "ENDPOINT_PAUSED" in message
    assert body["detail"] not in message
    assert "max workers >= 1" in message
    assert "ep-17" in message
    assert "private journal" not in message
    assert "secret-must-not-appear" not in message
    assert raised.value.retryable
    assert not raised.value.transient


def test_http_error_body_is_bounded_and_non_json_502_is_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-only")
    response_body = b"x" * (1024 * 1024)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(502, content=response_body))
    ) as client:
        with pytest.raises(ProviderError) as raised:
            InferenceProvider(settings(), client).generate(
                "cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "source"
            )
    assert "HTTP 502" in str(raised.value)
    assert raised.value.retryable
    assert raised.value.transient
    assert len(str(raised.value)) < 500


def test_http_401_is_nonretryable_and_names_key_without_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "secret-value")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401))) as client:
        with pytest.raises(ProviderError) as raised:
            InferenceProvider(settings(), client).generate(
                "cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "source"
            )
    assert "RUNPOD_API_KEY" in str(raised.value)
    assert "secret-value" not in str(raised.value)
    assert not raised.value.retryable


def test_http_401_uses_route_provider_kind_for_credential_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("RUNPOD_API_KEY", "runpod-secret")
    transport = httpx.MockTransport(lambda _: httpx.Response(401))
    with httpx.Client(transport=transport) as client:
        adapter = InferenceProvider(settings(), client, openai_transport=transport)
        with pytest.raises(ProviderError) as raised:
            adapter.generate(
                "cleanup",
                TaskRoute(provider="openai", base_url="https://api.runpod.ai/v2/local/v1"),
                "source",
            )
    message = str(raised.value)
    assert "OPENAI_API_KEY" in message
    assert "RUNPOD_API_KEY" not in message
    assert "openai-secret" not in message
    assert "runpod-secret" not in message


def test_openai_http_errors_include_endpoint_and_allowlisted_code() -> None:
    body = {"error": "quota exceeded", "code": "QUOTA_LIMIT"}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as raised:
            InferenceProvider(
                settings(), client, openai_transport=httpx.MockTransport(handler)
            ).generate(
                "cleanup",
                TaskRoute(provider="openai", base_url="http://local/v1"),
                "source",
            )
    assert "HTTP 429" in str(raised.value)
    assert "QUOTA_LIMIT" in str(raised.value)
    assert "quota exceeded" not in str(raised.value)
    assert "http://local/v1" in str(raised.value)
    assert raised.value.transient


def test_provider_echoed_journal_content_and_credentials_are_never_logged_or_returned(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    journal_text = "private-journal-echo-9f71"
    credential = "secret-provider-credential-cd21"
    monkeypatch.setenv("OPENAI_API_KEY", credential)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "code": journal_text,
                "detail": f"Rejected content: {journal_text}; credential={credential}",
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        provider = InferenceProvider(settings(), client, openai_transport=transport)
        with pytest.raises(ProviderError) as raised:
            provider.generate(
                "cleanup",
                TaskRoute(provider="openai", base_url="http://local/v1"),
                journal_text,
            )
    diagnostics = str(raised.value) + caplog.text
    assert "HTTP 403" in str(raised.value)
    assert "OPENAI_API_KEY" in str(raised.value)
    assert journal_text not in diagnostics
    assert credential not in diagnostics


def test_status_transient_failures_reset_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from msj import providers

    monkeypatch.setenv("RUNPOD_API_KEY", "test-only")
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    outcomes = iter([503, 200, 429, 200])
    status_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_calls
        if request.url.path.endswith("/run"):
            return httpx.Response(200, json={"id": "job", "status": "IN_PROGRESS"})
        if "/status/" in request.url.path:
            status_calls += 1
            outcome = next(outcomes)
            if outcome == 200:
                completed = status_calls == 4
                state: dict[str, Any] = {
                    "id": "job",
                    "status": "COMPLETED" if completed else "IN_PROGRESS",
                }
                if completed:
                    state["output"] = {"choices": [{"message": {"content": "ok"}}]}
                return httpx.Response(200, json=state)
            return httpx.Response(outcome)
        return httpx.Response(200, json={"status": "CANCELLED"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = InferenceProvider(settings(runpod_status_max_failures=1), client).generate(
            "cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "source"
        )
    assert result.text == "ok"
    assert status_calls == 4


def test_status_failure_budget_cancels_after_n_plus_one_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from msj import providers

    monkeypatch.setenv("RUNPOD_API_KEY", "test-only")
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/run"):
            return httpx.Response(200, json={"id": "job", "status": "IN_PROGRESS"})
        if "/status/" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(200, json={"id": "job", "status": "CANCELLED"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = InferenceProvider(settings(runpod_status_max_failures=2), client)
        with pytest.raises(ProviderError, match="status polling failed after 3"):
            provider.generate("cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "source")
    assert sum("/status/" in path for path in calls) == 3
    assert any("/cancel/" in path for path in calls)


def test_nontransient_status_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-only")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/run"):
            return httpx.Response(200, json={"id": "job", "status": "IN_PROGRESS"})
        if "/status/" in request.url.path:
            return httpx.Response(401)
        return httpx.Response(200, json={"id": "job", "status": "CANCELLED"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="HTTP 401"):
            InferenceProvider(settings(), client).generate(
                "cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "source"
            )
    assert sum("/status/" in path for path in calls) == 1
    assert any("/cancel/" in path for path in calls)


def test_status_413_keeps_its_nonretryable_error_if_cancel_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-only")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/run"):
            return httpx.Response(200, json={"id": "job", "status": "IN_PROGRESS"})
        return httpx.Response(413)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="request too large") as raised:
            InferenceProvider(settings(), client).generate(
                "cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "source"
            )
    assert not raised.value.retryable
    assert any("/cancel/" in path for path in calls)


def test_completed_runpod_job_wins_over_elapsed_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from msj import providers

    monkeypatch.setenv("RUNPOD_API_KEY", "test-only")
    now = [0.0]
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        providers.time, "sleep", lambda duration: now.__setitem__(0, now[0] + duration)
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/run"):
            return httpx.Response(200, json={"id": "job", "status": "IN_PROGRESS"})
        if "/status/" in request.url.path:
            now[0] = 10.0
            return httpx.Response(
                200,
                json={
                    "id": "job",
                    "status": "COMPLETED",
                    "output": {"choices": [{"message": {"content": "done"}}]},
                },
            )
        return httpx.Response(200, json={"status": "CANCELLED"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = InferenceProvider(settings(provider_timeout_s=5, lease_s=40), client).generate(
            "cleanup", TaskRoute(provider="runpod", endpoint_id="ep"), "source"
        )
    assert result.text == "done"
    assert not any("/cancel/" in path for path in calls)


@pytest.mark.parametrize("slow_response", [False, True])
def test_openai_deadline_cancels_stalled_and_trickled_responses(slow_response: bool) -> None:
    cancelled: list[bool] = []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                yield b'{"model":"local","choices":[{"message":{"content":"'
                await asyncio.sleep(0.2)
                yield b'late"}}]}'
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

    async def handler(request: httpx.Request) -> httpx.Response:
        if slow_response:
            return httpx.Response(200, stream=SlowBody())
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return httpx.Response(
            200, json={"model": "local", "choices": [{"message": {"content": "late"}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client:
        adapter = InferenceProvider(
            settings(provider_timeout_s=0.02, lease_s=30.1),
            client,
            openai_transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderError, match="timed out") as raised:
            adapter.generate("cleanup", TaskRoute(provider="openai"), "source")
    assert raised.value.retryable
    assert cancelled
