"""Wire formats for faster-whisper, llama.cpp passthrough, and local chat servers."""

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Protocol, TypedDict

import httpx

from .runpod_client import BenchmarkError, JobResult, RunPodClient, request_json


class Message(TypedDict):
    role: str
    content: str


@dataclass(frozen=True)
class Generation:
    text: str
    model: str | None
    wall_time_s: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    delay_time_ms: float | None = None
    execution_time_ms: float | None = None
    worker_id: str | None = None
    job_id: str | None = None
    finish_reason: str | None = None


class LLMBackend(Protocol):
    def generate(self, messages: Sequence[Message], *, max_tokens: int,
                 schema: Mapping[str, Any] | None = None) -> Generation: ...


def chat_body(messages: Sequence[Message], model: str, max_tokens: int,
              schema: Mapping[str, Any] | None, *, passthrough: bool) -> dict[str, Any]:
    if max_tokens <= 0:
        raise BenchmarkError("max_tokens must be positive")
    body: dict[str, Any] = {"model": model, "messages": list(messages),
                            "max_tokens": max_tokens, "temperature": 0, "stream": False}
    template = {"chat_template_kwargs": {"enable_thinking": False}}
    if passthrough:
        body["extra_body"] = template
    else:
        # extra_body is an SDK argument; direct OpenAI-compatible HTTP flattens it.
        body.update(template)
    if schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "journal_result", "strict": True, "schema": dict(schema)}}
    return body


def parse_chat(data: Any, wall_time_s: float, job: JobResult | None = None) -> Generation:
    if isinstance(data, str):
        import json
        try:
            data = json.loads(data)
        except ValueError:
            raise BenchmarkError("LLM worker returned invalid JSON") from None
    # Some passthrough worker releases wrap the HTTP response in a body field.
    if isinstance(data, dict) and "choices" not in data and "body" in data:
        return parse_chat(data["body"], wall_time_s, job)
    try:
        choice = data["choices"][0]
        text = choice["message"]["content"]
        model = data.get("model")
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise BenchmarkError("LLM response lacks choices[0].message.content") from None
    if not isinstance(text, str) or (model is not None and not isinstance(model, str)):
        raise BenchmarkError("LLM content/model has an invalid type; check for refusals or unsupported schemas")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise BenchmarkError("LLM response has an invalid finish_reason")
    for value in (prompt_tokens, completion_tokens):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise BenchmarkError("LLM response has invalid token usage")
    return Generation(text, model, wall_time_s, prompt_tokens, completion_tokens,
                      job.delay_time_ms if job else None, job.execution_time_ms if job else None,
                      job.worker_id if job else None, job.job_id if job else None, finish_reason)


class RunPodLLM:
    def __init__(self, client: RunPodClient, model: str = "any") -> None:
        self._client = client
        self._model = model

    def generate(self, messages: Sequence[Message], *, max_tokens: int,
                 schema: Mapping[str, Any] | None = None) -> Generation:
        body = chat_body(messages, self._model, max_tokens, schema, passthrough=True)
        job = self._client.run({"openai_route": "/v1/chat/completions", "openai_input": body})
        return parse_chat(job.output, job.wall_time_s, job)


class OpenAIBackend:
    def __init__(self, http: httpx.Client, *, base_url: str, model: str = "local",
                 api_key: str | None = None, timeout_s: float = 300) -> None:
        self._http = http
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout_s = timeout_s

    def generate(self, messages: Sequence[Message], *, max_tokens: int,
                 schema: Mapping[str, Any] | None = None) -> Generation:
        started = time.monotonic()
        data = request_json(self._http, "POST", self._url, headers=self._headers,
                            json=chat_body(messages, self._model, max_tokens, schema, passthrough=False),
                            timeout=self._timeout_s)
        return parse_chat(data, time.monotonic() - started)


class WhisperBackend:
    def __init__(self, client: RunPodClient, model: str = "turbo") -> None:
        self._client = client
        self._model = model

    def transcribe(self, audio: Path | str) -> Generation:
        payload: dict[str, Any] = {"model": self._model}
        if isinstance(audio, str) and audio.startswith(("https://", "http://")):
            payload["audio"] = audio
        else:
            try:
                payload["audio_base64"] = base64.b64encode(Path(audio).read_bytes()).decode("ascii")
            except OSError:
                raise BenchmarkError("Cannot read audio file; check its path and permissions") from None
        job = self._client.run(payload)
        output = job.output
        if not isinstance(output, dict):
            raise BenchmarkError("Whisper output must be a JSON object")
        text = output.get("transcription", output.get("text"))
        if text is None and isinstance(output.get("segments"), list):
            segments = output["segments"]
            if not all(isinstance(s, dict) and isinstance(s.get("text"), str) for s in segments):
                raise BenchmarkError("Whisper returned invalid segments")
            text = " ".join(s["text"].strip() for s in segments)
        if not isinstance(text, str):
            raise BenchmarkError("Whisper response contains no transcription, text, or usable segments")
        model = output.get("model")
        if model is not None and not isinstance(model, str):
            raise BenchmarkError("Whisper returned invalid model metadata")
        return Generation(text, model, job.wall_time_s, delay_time_ms=job.delay_time_ms,
                          execution_time_ms=job.execution_time_ms, worker_id=job.worker_id, job_id=job.job_id)
