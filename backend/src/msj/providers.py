"""Inference adapters for RunPod Serverless, OpenAI-compatible servers, and tests."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .config import Settings, TaskKind, TaskRoute


class ProviderError(RuntimeError):
    """Safe, actionable inference failure without remote response details."""

    def __init__(self, message: str, *, retryable: bool = True, transient: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.transient = transient


@dataclass(frozen=True)
class Generation:
    text: str
    model: str | None


class Provider(Protocol):
    def generate(
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        *,
        candidate: str | None = None,
        audio: bytes | None = None,
    ) -> Generation: ...


def ensure_model(generation: Generation, route: TaskRoute) -> Generation:
    """Reject artifacts produced by an unexpected or unidentified worker model."""
    expected = route.expect_model
    if expected is not None and generation.model != expected:
        actual = generation.model if generation.model is not None else "missing model metadata"
        raise ProviderError(
            f"Stale worker or wrong model: expected {expected!r}, received {actual!r}"
        )
    return generation


def _request_json(http: httpx.Client, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = http.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise ProviderError(
            f"{method} request failed; check connectivity and provider configuration",
            transient=True,
        ) from None
    if not response.is_success:
        raise _http_status_error(response.status_code)
    try:
        value: Any = response.json()
    except ValueError, RecursionError:
        raise ProviderError("Provider returned invalid JSON") from None
    if not isinstance(value, dict):
        raise ProviderError("Provider response must be a JSON object")
    return value


def _parse_chat(value: Any) -> Generation:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ProviderError("LLM worker returned invalid JSON") from None
    if isinstance(value, list) and len(value) == 1:
        return _parse_chat(value[0])
    if isinstance(value, dict) and "choices" not in value and "body" in value:
        return _parse_chat(value["body"])
    try:
        choice = value["choices"][0]
        content = choice["message"]["content"]
        model = value.get("model")
        finish_reason = choice.get("finish_reason")
    except KeyError, IndexError, TypeError, AttributeError:
        raise ProviderError("LLM response lacks choices[0].message.content") from None
    if not isinstance(content, str) or (model is not None and not isinstance(model, str)):
        raise ProviderError("LLM response has invalid content or model metadata")
    if isinstance(finish_reason, str) and finish_reason in {"length", "content_filter"}:
        raise ProviderError(
            "LLM output is incomplete or filtered; increase max_tokens or check model"
        )
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise ProviderError("LLM response has invalid finish_reason metadata")
    return Generation(content, model)


def _chat_body(
    route: TaskRoute,
    kind: TaskKind,
    source: str,
    candidate: str | None,
    *,
    runpod: bool,
) -> dict[str, Any]:
    from importlib.resources import files

    if kind == "transcribe":
        raise ProviderError("Transcription requires audio input")
    prompt = files("msj.resources").joinpath(f"{kind}.txt").read_text(encoding="utf-8")
    payload: dict[str, str] = {"source": source}
    if kind == "review":
        if candidate is None:
            raise ProviderError("Review requires cleaned candidate text")
        payload["candidate"] = candidate
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    body: dict[str, Any] = {
        "model": route.model,
        "messages": messages,
        "max_tokens": route.max_tokens,
        "temperature": 0,
        "stream": False,
    }
    if runpod:
        body["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    else:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if kind == "extract":
        schema = json.loads(
            files("msj.resources").joinpath("extraction.schema.json").read_text(encoding="utf-8")
        )
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "journal_result", "strict": True, "schema": schema},
        }
    if kind == "review":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "journal_result",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "unsupported_claims": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["unsupported_claims"],
                },
            },
        }
    return body


class InferenceProvider:
    """Dispatch configured tasks to deterministic, RunPod, or OpenAI providers."""

    def __init__(
        self,
        settings: Settings,
        http: httpx.Client,
        *,
        openai_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._http = http
        self._openai_transport = openai_transport

    def generate(
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        *,
        candidate: str | None = None,
        audio: bytes | None = None,
    ) -> Generation:
        match route.provider:
            case "fake":
                generation = self._fake(kind, route, source, candidate, audio)
            case "runpod":
                generation = self._runpod(kind, route, source, candidate, audio)
            case "openai":
                generation = self._openai(kind, route, source, candidate, audio)
        return ensure_model(generation, route)

    @staticmethod
    def _fake(
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        candidate: str | None,
        audio: bytes | None,
    ) -> Generation:
        match kind:
            case "transcribe":
                if audio is None:
                    raise ProviderError("Transcription requires audio input")
                text = "[fake transcript]"
            case "cleanup" | "summarize":
                text = source
            case "review":
                if candidate is None:
                    raise ProviderError("Review requires cleaned candidate text")
                text = '{"unsupported_claims": []}'
            case "extract":
                text = json.dumps(
                    {
                        "projects": [],
                        "skills_technologies": [],
                        "accomplishments": [],
                        "people_roles": [],
                        "star_fragments": [],
                        "tags": [],
                    }
                )
        return Generation(text, "fake")

    def _runpod(
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        candidate: str | None,
        audio: bytes | None,
    ) -> Generation:
        if kind == "transcribe":
            if audio is None:
                raise ProviderError("Transcription requires audio input")
            payload: dict[str, Any] = {
                "audio_base64": base64.b64encode(audio).decode("ascii"),
                "model": route.model,
            }
            request_body = json.dumps(
                {"input": payload}, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(request_body) > self._settings.runpod_max_payload_bytes:
                raise ProviderError(
                    "audio too large for RunPod request; record in Opus/compressed format or "
                    "split the recording",
                    retryable=False,
                )
            submission: dict[str, Any] = {"content": request_body}
        else:
            payload = {
                "openai_route": "/v1/chat/completions",
                "openai_input": _chat_body(route, kind, source, candidate, runpod=True),
            }
            submission = {"json": {"input": payload}}
        key = os.environ.get("RUNPOD_API_KEY")
        if not key or not route.endpoint_id:
            raise ProviderError("RUNPOD_API_KEY and endpoint_id are required for RunPod tasks")
        endpoint = f"https://api.runpod.ai/v2/{quote(route.endpoint_id, safe='')}"
        headers = {"Authorization": f"Bearer {key}"}
        submission["headers"] = (
            {**headers, "Content-Type": "application/json"} if kind == "transcribe" else headers
        )
        started = time.monotonic()
        deadline = started + self._settings.provider_timeout_s
        state = _request_json(
            self._http,
            "POST",
            f"{endpoint}/run",
            timeout=min(30, self._settings.provider_timeout_s),
            **submission,
        )
        job_id = state.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise ProviderError("RunPod submission returned no job ID")
        status_url = f"{endpoint}/status/{quote(job_id, safe='')}"
        pending = True
        interval = self._settings.poll_initial_s
        status_failures = 0

        def cancel() -> None:
            try:
                _ = _request_json(
                    self._http,
                    "POST",
                    f"{endpoint}/cancel/{quote(job_id, safe='')}",
                    headers=headers,
                    timeout=min(30, self._settings.provider_timeout_s),
                )
            except ProviderError:
                raise ProviderError(
                    "RunPod monitoring failed and cancellation could not be confirmed"
                ) from None

        try:
            while True:
                status = state.get("status")
                if not isinstance(status, str):
                    raise ProviderError("RunPod returned a missing or non-string job status")
                if status == "COMPLETED":
                    pending = False
                    if "output" not in state:
                        raise ProviderError("Completed RunPod job has no output")
                    if kind == "transcribe":
                        generation = self._parse_transcription(state["output"])
                    else:
                        generation = _parse_chat(state["output"])
                    return generation
                if status in {"FAILED", "TIMED_OUT", "CANCELLED"}:
                    pending = False
                    raise ProviderError(f"RunPod job ended with status {status}")
                if time.monotonic() >= deadline:
                    pending = False
                    cancel()
                    raise ProviderError("RunPod job timed out; cancellation requested")
                if status not in {"IN_QUEUE", "IN_PROGRESS"}:
                    raise ProviderError("RunPod returned a missing or unknown job status")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    pending = False
                    cancel()
                    raise ProviderError("RunPod job timed out; cancellation requested")
                time.sleep(min(interval, remaining))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    continue
                try:
                    state = _request_json(
                        self._http, "GET", status_url, headers=headers, timeout=min(30, remaining)
                    )
                    status_failures = 0
                except ProviderError as exc:
                    if not exc.transient:
                        raise
                    status_failures += 1
                    if status_failures > self._settings.runpod_status_max_failures:
                        raise ProviderError(
                            "RunPod status polling failed after "
                            f"{status_failures} consecutive transient failures",
                            retryable=True,
                        ) from None
                    if time.monotonic() >= deadline:
                        raise ProviderError("RunPod job timed out while polling status") from None
                    interval = min(interval * 1.5, self._settings.poll_max_s)
                    continue
                interval = min(interval * 1.5, self._settings.poll_max_s)
        except ProviderError as exc:
            if pending:
                try:
                    cancel()
                except ProviderError:
                    if not exc.retryable:
                        raise exc from None
                    raise
            raise
        except KeyboardInterrupt:
            if pending:
                cancel()
            raise

    @staticmethod
    def _parse_transcription(value: Any) -> Generation:
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if not isinstance(value, dict):
            raise ProviderError("Whisper output must be a JSON object")
        text = value.get("transcription", value.get("text"))
        if text is None and isinstance(value.get("segments"), list):
            segments = value["segments"]
            if not all(
                isinstance(segment, dict) and isinstance(segment.get("text"), str)
                for segment in segments
            ):
                raise ProviderError("Whisper returned invalid segments")
            text = " ".join(segment["text"].strip() for segment in segments)
        model = value.get("model")
        if not isinstance(text, str) or (model is not None and not isinstance(model, str)):
            raise ProviderError(
                "Whisper response contains no transcription or has invalid model metadata"
            )
        return Generation(text, model)

    def _openai(
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        candidate: str | None,
        audio: bytes | None,
    ) -> Generation:
        try:
            return asyncio.run(self._openai_request(kind, route, source, candidate, audio))
        except TimeoutError:
            raise ProviderError(
                f"OpenAI-compatible request timed out after "
                f"{self._settings.provider_timeout_s:g} seconds"
            ) from None

    async def _openai_request(
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        candidate: str | None,
        audio: bytes | None,
    ) -> Generation:
        base = route.base_url.rstrip("/")
        timeout = self._settings.provider_timeout_s
        headers: dict[str, str] = {}
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(
                    transport=self._openai_transport,
                    timeout=httpx.Timeout(timeout),
                    trust_env=False,
                ) as client:
                    if kind == "transcribe":
                        if audio is None:
                            raise ProviderError("Transcription requires audio input")
                        suffix = Path(source).suffix.lower()
                        media_type = {
                            ".wav": "audio/wav",
                            ".mp3": "audio/mpeg",
                            ".m4a": "audio/mp4",
                            ".mp4": "audio/mp4",
                            ".webm": "audio/webm",
                            ".ogg": "audio/ogg",
                            ".flac": "audio/flac",
                        }.get(suffix, "audio/wav")
                        filename = "entry" + (
                            suffix
                            if suffix in {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".flac"}
                            else ".wav"
                        )
                        response = await client.post(
                            f"{base}/audio/transcriptions",
                            headers=headers,
                            data={"model": route.model},
                            files={"file": (filename, audio, media_type)},
                        )
                    else:
                        body = _chat_body(route, kind, source, candidate, runpod=False)
                        response = await client.post(
                            f"{base}/chat/completions", headers=headers, json=body
                        )
                    if not response.is_success:
                        raise _http_status_error(response.status_code)
                    value: Any = response.json()
        except httpx.HTTPError as exc:
            if isinstance(
                exc,
                (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectTimeout, httpx.PoolTimeout),
            ):
                raise ProviderError(
                    "OpenAI-compatible request timed out; check provider availability",
                    transient=True,
                ) from None
            raise ProviderError(
                "OpenAI-compatible request failed; check connectivity and endpoint configuration",
                transient=True,
            ) from None
        except ValueError, RecursionError:
            raise ProviderError("Provider returned invalid JSON") from None
        if time.monotonic() - started > timeout:
            raise ProviderError(f"OpenAI-compatible request timed out after {timeout:g} seconds")
        if kind == "transcribe":
            if not isinstance(value, dict) or not isinstance(value.get("text"), str):
                raise ProviderError("Transcription response must contain a text field")
            model = value.get("model")
            if model is not None and not isinstance(model, str):
                raise ProviderError("Transcription response has invalid model metadata")
            return Generation(value["text"], model)
        return _parse_chat(value)


def _http_status_error(status_code: int) -> ProviderError:
    if status_code == 413:
        return ProviderError(
            "Provider request too large (HTTP 413); reduce audio or text size",
            retryable=False,
        )
    return ProviderError(
        f"Provider returned HTTP {status_code}; check credentials and endpoint configuration",
        transient=status_code in {408, 429} or status_code >= 500,
    )
