"""Inference adapters for RunPod Serverless, OpenAI-compatible servers, and tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Protocol, ReadOnly, TypedDict
from urllib.parse import quote

import httpx

from .config import Settings, TaskKind, TaskRoute
from .diagnostics import SafeMessageError

_logger = logging.getLogger(__name__)
_ERROR_BODY_LIMIT = 8 * 1024
_SAFE_PROVIDER_CODES = frozenset(
    {"ENDPOINT_PAUSED", "QUOTA_LIMIT", "RATE_LIMITED", "INVALID_API_KEY", "MODEL_NOT_FOUND"}
)


class Message(TypedDict):
    role: ReadOnly[str]
    content: ReadOnly[str]


class ProviderError(RuntimeError):
    """An inference failure whose text is private unless explicitly sanitized."""

    def __init__(self, message: str, *, retryable: bool = True, transient: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.transient = transient


class SafeProviderError(ProviderError, SafeMessageError):
    """Built-in error assembled from fixed templates and non-content metadata."""

    code = "PROVIDER_ERROR"

    def __init__(self, message: str, *, retryable: bool = True, transient: bool = False) -> None:
        super().__init__(message, retryable=retryable, transient=transient)
        self.message = message


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
        messages: Sequence[Message] | None = None,
    ) -> Generation: ...


_MODEL_NAME = re.compile(r"[A-Za-z0-9._:/@+-]{1,128}")


def _log_runpod_finish(
    kind: TaskKind, job_id: str, status: str, state: Mapping[str, Any], started: float
) -> None:
    """Log one line per finished RunPod job with RunPod's queue and execution times."""
    assert job_id
    delay = state.get("delayTime")
    execution = state.get("executionTime")
    _logger.info(
        "RunPod %s job %s %s after %.1f s (queue %s ms, execution %s ms)",
        kind,
        job_id,
        status,
        time.monotonic() - started,
        delay if isinstance(delay, int) else "?",
        execution if isinstance(execution, int) else "?",
    )


def ensure_model(generation: Generation, route: TaskRoute) -> Generation:
    """Reject artifacts produced by an unexpected or unidentified worker model."""
    expected = route.expect_model
    if expected is not None and generation.model != expected:
        observed = generation.model
        if observed is None:
            shown = "no model name"
        elif _MODEL_NAME.fullmatch(observed):
            shown = repr(observed)
        else:
            shown = "a value that is not a model name (withheld)"
        raise SafeProviderError(
            f"Stale worker or wrong model: expected {expected!r} from the worker but it "
            f"reported {shown}; update expect_model for this task or redeploy the endpoint"
        )
    return generation


@cache
def _prompt(kind: TaskKind) -> str:
    from importlib.resources import files

    return files("msj.resources").joinpath(f"{kind}.txt").read_text(encoding="utf-8")


@cache
def _revision_template() -> str:
    from importlib.resources import files

    return files("msj.resources").joinpath("revise.txt").read_text(encoding="utf-8")


def prompt_digest(kind: TaskKind, *, revision: bool = False) -> str:
    """Hash the immutable prompt strings used by this worker's message builder."""
    content = _prompt(kind).encode("utf-8")
    if revision:
        content += b"\n" + _revision_template().encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def initial_messages(
    kind: TaskKind, source: str, *, candidate: str | None = None
) -> tuple[Message, ...]:
    """Build the initial producer or reviewer conversation."""
    if kind == "transcribe":
        raise SafeProviderError("Transcription does not use chat messages")
    payload: dict[str, str] = {"source": source}
    if kind == "review":
        if candidate is None:
            raise SafeProviderError("Review requires a candidate")
        payload["candidate"] = candidate
    return (
        {"role": "system", "content": _prompt(kind)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    )


def revision_messages(
    kind: TaskKind, source: str, previous: str, claims: Sequence[str]
) -> tuple[Message, ...]:
    """Build a producer revision conversation from source and reviewer claims."""
    if kind in {"review", "transcribe"}:
        raise SafeProviderError("Only cleanup, summarize, and extract outputs can be revised")
    request = {
        "revision_request": _revision_template(),
        "unsupported_claims": list(claims),
    }
    original = initial_messages(kind, source)
    return (
        original[0],
        original[1],
        {"role": "assistant", "content": previous},
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
    )


def _request_json(
    http: httpx.Client,
    method: str,
    url: str,
    *,
    endpoint_id: str,
    provider_kind: str,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        response = http.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise SafeProviderError(
            f"{method} request failed; check connectivity and provider configuration",
            transient=True,
        ) from None
    if not response.is_success:
        raise _http_status_error(response.status_code, response, endpoint_id, provider_kind)
    try:
        value: Any = response.json()
    except ValueError, RecursionError:
        raise SafeProviderError("Provider returned invalid JSON") from None
    if not isinstance(value, dict):
        raise SafeProviderError("Provider response must be a JSON object")
    return value


def _parse_chat(value: Any) -> Generation:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise SafeProviderError("LLM worker returned invalid JSON") from None
    if isinstance(value, list) and len(value) == 1:
        return _parse_chat(value[0])
    if not isinstance(value, dict):
        raise SafeProviderError("LLM response lacks choices[0].message.content")
    if "choices" not in value and "body" in value:
        return _parse_chat(value["body"])
    try:
        choice = value["choices"][0]
        content = choice["message"]["content"]
        model = value.get("model")
        finish_reason = choice.get("finish_reason")
    except KeyError, IndexError, TypeError, AttributeError:
        raise SafeProviderError("LLM response lacks choices[0].message.content") from None
    if not isinstance(content, str) or (model is not None and not isinstance(model, str)):
        raise SafeProviderError("LLM response has invalid content or model metadata")
    if isinstance(finish_reason, str) and finish_reason in {"length", "content_filter"}:
        raise SafeProviderError(
            "LLM output is incomplete or filtered; increase max_tokens or check model"
        )
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise SafeProviderError("LLM response has invalid finish_reason metadata")
    return Generation(content, model)


def _chat_body(
    route: TaskRoute,
    kind: TaskKind,
    source: str,
    candidate: str | None,
    *,
    runpod: bool,
    messages: Sequence[Message] | None = None,
) -> dict[str, Any]:
    if kind == "transcribe":
        raise SafeProviderError("Transcription requires audio input")
    conversation = (
        tuple(dict(turn) for turn in messages)
        if messages is not None
        else initial_messages(kind, source, candidate=candidate)
    )
    body: dict[str, Any] = {
        "model": route.model,
        "messages": conversation,
        "max_tokens": route.max_tokens,
        "temperature": 0,
        "stream": False,
    }
    if runpod:
        body["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    else:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if kind in {"extract", "compile"}:
        from importlib.resources import files

        schema_name = "compile.schema.json" if kind == "compile" else "extraction.schema.json"
        schema = json.loads(
            files("msj.resources").joinpath(schema_name).read_text(encoding="utf-8")
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
        messages: Sequence[Message] | None = None,
    ) -> Generation:
        match route.provider:
            case "fake":
                generation = self._fake(kind, route, source, candidate, audio, messages)
            case "runpod":
                generation = self._runpod(kind, route, source, candidate, audio, messages)
            case "openai":
                generation = self._openai(kind, route, source, candidate, audio, messages)
        return ensure_model(generation, route)

    @staticmethod
    def _fake(  # noqa: PLR0917
        kind: TaskKind,
        route: TaskRoute,  # noqa: ARG004 - same signature as the other provider adapters
        source: str,
        candidate: str | None,
        audio: bytes | None,
        messages: Sequence[Message] | None,
    ) -> Generation:
        if messages is not None and kind != "review":
            prior = next(
                (turn["content"] for turn in reversed(messages) if turn["role"] == "assistant"),
                None,
            )
            if prior is None:
                raise SafeProviderError("Revision conversation has no previous assistant output")
            return Generation(prior, "fake")
        match kind:
            case "transcribe":
                if audio is None:
                    raise SafeProviderError("Transcription requires audio input")
                text = "[fake transcript]"
            case "cleanup" | "summarize":
                text = source
            case "review":
                if candidate is None:
                    raise SafeProviderError("Review requires cleaned candidate text")
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
            case "compile":
                text = json.dumps(
                    {
                        "resume_bullets": [],
                        "star_stories": [],
                        "technical_topics": [],
                        "open_threads": [],
                    }
                )
        return Generation(text, "fake")

    def _runpod(  # noqa: C901, PLR0912, PLR0915, PLR0917
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        candidate: str | None,
        audio: bytes | None,
        messages: Sequence[Message] | None,
    ) -> Generation:
        if kind == "transcribe":
            if audio is None:
                raise SafeProviderError("Transcription requires audio input")
            payload: dict[str, Any] = {
                "audio_base64": base64.b64encode(audio).decode("ascii"),
                "model": route.model,
            }
            request_body = json.dumps(
                {"input": payload}, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(request_body) > self._settings.runpod_max_payload_bytes:
                raise SafeProviderError(
                    "audio too large for RunPod request; record in Opus/compressed format or "
                    "split the recording",
                    retryable=False,
                )
            submission: dict[str, Any] = {"content": request_body}
        else:
            payload = {
                "openai_route": "/v1/chat/completions",
                "openai_input": _chat_body(
                    route, kind, source, candidate, runpod=True, messages=messages
                ),
            }
            submission = {"json": {"input": payload}}
        key = os.environ.get("RUNPOD_API_KEY")
        if not key or not route.endpoint_id:
            raise SafeProviderError("RUNPOD_API_KEY and endpoint_id are required for RunPod tasks")
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
            endpoint_id=route.endpoint_id or "",
            provider_kind=route.provider,
            timeout=min(30, self._settings.provider_timeout_s),
            **submission,
        )
        job_id = state.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise SafeProviderError("RunPod submission returned no job ID")
        _logger.info("RunPod %s job %s submitted to endpoint %s", kind, job_id, route.endpoint_id)
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
                    endpoint_id=route.endpoint_id or "",
                    provider_kind=route.provider,
                    headers=headers,
                    timeout=min(30, self._settings.provider_timeout_s),
                )
            except ProviderError:
                raise SafeProviderError(
                    "RunPod monitoring failed and cancellation could not be confirmed"
                ) from None

        try:
            while True:
                status = state.get("status")
                if not isinstance(status, str):
                    raise SafeProviderError("RunPod returned a missing or non-string job status")
                if status == "COMPLETED":
                    pending = False
                    _log_runpod_finish(kind, job_id, status, state, started)
                    if "output" not in state:
                        raise SafeProviderError("Completed RunPod job has no output")
                    if kind == "transcribe":
                        generation = self._parse_transcription(state["output"])
                    else:
                        generation = _parse_chat(state["output"])
                    return generation
                if status in {"FAILED", "TIMED_OUT", "CANCELLED"}:
                    pending = False
                    _log_runpod_finish(kind, job_id, status, state, started)
                    raise SafeProviderError(
                        f"RunPod job {job_id} on endpoint {route.endpoint_id} ended with status "
                        f"{status}; open the job in the RunPod console for the worker's error"
                    )
                if time.monotonic() >= deadline:
                    pending = False
                    cancel()
                    raise SafeProviderError("RunPod job timed out; cancellation requested")
                if status not in {"IN_QUEUE", "IN_PROGRESS"}:
                    raise SafeProviderError("RunPod returned a missing or unknown job status")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    pending = False
                    cancel()
                    raise SafeProviderError("RunPod job timed out; cancellation requested")
                time.sleep(min(interval, remaining))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    continue
                try:
                    state = _request_json(
                        self._http,
                        "GET",
                        status_url,
                        endpoint_id=route.endpoint_id or "",
                        provider_kind=route.provider,
                        headers=headers,
                        timeout=min(30, remaining),
                    )
                    status_failures = 0
                except ProviderError as exc:
                    if not exc.transient:
                        raise
                    status_failures += 1
                    if status_failures > self._settings.runpod_status_max_failures:
                        raise SafeProviderError(
                            "RunPod status polling failed after "
                            f"{status_failures} consecutive transient failures",
                            retryable=True,
                        ) from None
                    if time.monotonic() >= deadline:
                        raise SafeProviderError(
                            "RunPod job timed out while polling status"
                        ) from None
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
            raise SafeProviderError("Whisper output must be a JSON object")
        text = value.get("transcription", value.get("text"))
        if text is None and isinstance(value.get("segments"), list):
            segments = value["segments"]
            if not all(
                isinstance(segment, dict) and isinstance(segment.get("text"), str)
                for segment in segments
            ):
                raise SafeProviderError("Whisper returned invalid segments")
            text = " ".join(segment["text"].strip() for segment in segments)
        model = value.get("model")
        if not isinstance(text, str) or (model is not None and not isinstance(model, str)):
            raise SafeProviderError(
                "Whisper response contains no transcription or has invalid model metadata"
            )
        return Generation(text, model)

    def _openai(  # noqa: PLR0917
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        candidate: str | None,
        audio: bytes | None,
        messages: Sequence[Message] | None,
    ) -> Generation:
        try:
            return asyncio.run(
                self._openai_request(kind, route, source, candidate, audio, messages)
            )
        except TimeoutError:
            raise SafeProviderError(
                f"OpenAI-compatible request timed out after "
                f"{self._settings.provider_timeout_s:g} seconds"
            ) from None

    async def _openai_request(  # noqa: C901, PLR0917
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        candidate: str | None,
        audio: bytes | None,
        messages: Sequence[Message] | None,
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
                            raise SafeProviderError("Transcription requires audio input")
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
                        body = _chat_body(
                            route, kind, source, candidate, runpod=False, messages=messages
                        )
                        response = await client.post(
                            f"{base}/chat/completions", headers=headers, json=body
                        )
                    if not response.is_success:
                        raise _http_status_error(
                            response.status_code,
                            response,
                            route.endpoint_id or route.base_url,
                            route.provider,
                        )
                    value: Any = response.json()
        except httpx.HTTPError as exc:
            if isinstance(
                exc,
                (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectTimeout, httpx.PoolTimeout),
            ):
                raise SafeProviderError(
                    "OpenAI-compatible request timed out; check provider availability",
                    transient=True,
                ) from None
            raise SafeProviderError(
                "OpenAI-compatible request failed; check connectivity and endpoint configuration",
                transient=True,
            ) from None
        except ValueError, RecursionError:
            raise SafeProviderError("Provider returned invalid JSON") from None
        if time.monotonic() - started > timeout:
            raise SafeProviderError(
                f"OpenAI-compatible request timed out after {timeout:g} seconds"
            )
        if kind == "transcribe":
            if not isinstance(value, dict) or not isinstance(value.get("text"), str):
                raise SafeProviderError("Transcription response must contain a text field")
            model = value.get("model")
            if model is not None and not isinstance(model, str):
                raise SafeProviderError("Transcription response has invalid model metadata")
            return Generation(value["text"], model)
        return _parse_chat(value)


def _provider_error_fields(response: httpx.Response) -> dict[str, str]:
    try:
        value: Any = json.loads(response.content[:_ERROR_BODY_LIMIT])
    except ValueError, RecursionError:
        return {}
    if not isinstance(value, dict):
        return {}
    code = value.get("code")
    if isinstance(code, str) and code in _SAFE_PROVIDER_CODES:
        return {"code": code}
    return {}


_HTTP_ERROR_GUIDANCE = {
    401: "API key rejected; check {key_name} is set and valid",
    403: "API key rejected; check {key_name} is set and valid",
    404: "endpoint or job not found; check the configured endpoint id",
    409: "conflict",
    413: "request too large (payload too large); reduce audio or text size",
    429: "rate limited; wait before retrying",
}


def _http_status_error(
    status_code: int, response: httpx.Response, endpoint_id: str, provider_kind: str
) -> SafeProviderError:
    fields = _provider_error_fields(response)
    code = fields.get("code")
    if status_code == 409 and code == "ENDPOINT_PAUSED":
        summary = (
            "endpoint paused and not accepting work; set max workers >= 1 in RunPod; "
            "if it already shows >= 1, re-save the workers setting"
        )
    else:
        summary = _HTTP_ERROR_GUIDANCE.get(
            status_code,
            "provider-side error" if status_code >= 500 else "provider request failed",
        )
    if status_code in {401, 403}:
        key_name = "RUNPOD_API_KEY" if provider_kind == "runpod" else "OPENAI_API_KEY"
        summary = summary.format(key_name=key_name)
    suffix = f"; provider code {code}" if code else ""
    _logger.warning(
        "Provider HTTP error endpoint_id=%s status=%d code=%s",
        endpoint_id,
        status_code,
        code or "-",
    )
    retryable = status_code not in {401, 403, 404, 413}
    transient = status_code in {408, 429} or status_code >= 500
    if status_code == 409 and code == "ENDPOINT_PAUSED":
        retryable = True
        transient = False
    return SafeProviderError(
        f"HTTP {status_code} for endpoint {endpoint_id}: {summary}{suffix}",
        retryable=retryable,
        transient=transient,
    )
