"""Provider regressions for payload caps, polling and total request deadlines."""

import asyncio
import base64
import json
from typing import Any

import httpx
import pytest

from msj.config import Settings, TaskRoute
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
    "provider,kind", [("runpod", "cleanup"), ("openai", "cleanup"), ("openai", "transcribe")]
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
                kind,
                TaskRoute(provider=provider, endpoint_id="ep", model="turbo"),
                "source",
                audio=b"audio" if kind == "transcribe" else None,
            )
    assert not raised.value.retryable


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
