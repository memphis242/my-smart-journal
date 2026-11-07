"""Synchronous RunPod jobs with a bounded polling deadline and explicit cancellation."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import logging
import math
import time
from typing import Any
from urllib.parse import quote

import httpx


class BenchmarkError(RuntimeError):
    """A safe, actionable error suitable for the CLI and result files."""


_logger = logging.getLogger(__name__)
_ERROR_BODY_LIMIT = 8 * 1024
_ERROR_FIELD_LIMIT = 300
_ERROR_FIELDS = ("code", "title", "detail", "error", "message")
_STATUS_GUIDANCE = {
    401: "API key rejected: check RUNPOD_API_KEY (or OPENAI_API_KEY) is set and valid.",
    403: "API key rejected: check RUNPOD_API_KEY (or OPENAI_API_KEY) is set and valid.",
    404: "Endpoint or job not found: check the configured endpoint ID.",
    413: "Payload too large; reduce the request size. This request is not retried.",
    429: "Provider rate limited the request; wait and retry.",
}


def _clean_provider_text(value: str) -> str:
    return "".join(char for char in value if char.isprintable())[:_ERROR_FIELD_LIMIT]


def _provider_error(response: httpx.Response) -> dict[str, str]:
    try:
        body = json.loads(response.content[:_ERROR_BODY_LIMIT])
    except (ValueError, RecursionError, UnicodeDecodeError):
        return {}
    if not isinstance(body, dict):
        return {}
    return {field: _clean_provider_text(body[field]) for field in _ERROR_FIELDS
            if isinstance(body.get(field), str) and _clean_provider_text(body[field])}


def _http_error_message(method: str, status: int, endpoint_id: str, info: Mapping[str, str]) -> str:
    code = info.get("code", "")
    detail = info.get("detail", "") or info.get("error", "") or info.get("message", "")
    title = info.get("title", "")
    if status == 409 and code == "ENDPOINT_PAUSED":
        reason = "Endpoint is paused and not accepting work. Set max workers >= 1 in RunPod; if it already shows >= 1, re-save the workers setting."
    elif status == 409:
        reason = "Provider conflict."
    elif status in _STATUS_GUIDANCE:
        reason = _STATUS_GUIDANCE[status]
    elif status >= 500:
        reason = "Provider-side error; retry later."
    else:
        reason = "Request failed; check the endpoint configuration."
    context = f"; provider code {code}" if code else ""
    if title:
        context += f"; provider title: {title}"
    if detail:
        context += f"; provider detail: {detail}"
    resource = f" for endpoint {endpoint_id}" if endpoint_id else ""
    return f"{method} returned HTTP {status}{context}{resource}. {reason}"


@dataclass(frozen=True)
class JobResult:
    output: Any
    job_id: str
    delay_time_ms: float | None
    execution_time_ms: float | None
    wall_time_s: float
    worker_id: str | None


def request_json(client: httpx.Client, method: str, url: str, *, endpoint_id: str = "", **kwargs: Any) -> dict[str, Any]:
    """Avoid reflecting remote response bodies, URLs, or authorization in errors."""
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise BenchmarkError(f"{method} request failed; check connectivity and endpoint configuration") from None
    if not response.is_success:
        provider_error = _provider_error(response)
        _logger.warning("RunPod HTTP error status=%s endpoint_id=%s provider_code=%s provider_detail=%s",
                        response.status_code, endpoint_id, provider_error.get("code", ""),
                        provider_error.get("detail", "") or provider_error.get("error", "") or provider_error.get("message", ""))
        raise BenchmarkError(_http_error_message(method, response.status_code, endpoint_id, provider_error))
    try:
        value = response.json()
    except (ValueError, RecursionError):
        raise BenchmarkError("Server returned invalid JSON") from None
    if not isinstance(value, dict):
        raise BenchmarkError("Server response must be a JSON object")
    return value


def optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise BenchmarkError(f"Server returned invalid {field}")
    return float(value)


class RunPodClient:
    def __init__(
        self, http: httpx.Client, *, api_key: str, endpoint_id: str,
        api_base: str = "https://api.runpod.ai/v2", timeout_s: float = 900,
        request_timeout_s: float = 30, poll_initial_s: float = 1,
        poll_max_s: float = 10, backoff: float = 1.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or not endpoint_id:
            raise BenchmarkError("RUNPOD_API_KEY and endpoint ID are required")
        if not all(math.isfinite(v) and v > 0 for v in (timeout_s, request_timeout_s, poll_initial_s, poll_max_s, backoff)):
            raise BenchmarkError("Timeouts and polling settings must be finite and positive")
        if backoff < 1 or poll_max_s < poll_initial_s:
            raise BenchmarkError("Backoff must be >= 1 and maximum poll interval >= initial interval")
        self._http = http
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._url = f"{api_base.rstrip('/')}/{quote(endpoint_id, safe='')}"
        self._endpoint_id = endpoint_id
        self._timeout_s = timeout_s
        self._request_timeout_s = request_timeout_s
        self._initial_s = poll_initial_s
        self._max_s = poll_max_s
        self._backoff = backoff
        self._clock = clock
        self._sleep = sleep

    def run(self, payload: Mapping[str, Any]) -> JobResult:
        started = self._clock()
        deadline = started + self._timeout_s
        state = request_json(self._http, "POST", f"{self._url}/run", headers=self._headers,
                             endpoint_id=self._endpoint_id,
                             json={"input": dict(payload)}, timeout=min(self._request_timeout_s, self._timeout_s))
        job_id = state.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise BenchmarkError("RunPod submission returned no job ID; inspect the endpoint dashboard")
        job_url = quote(job_id, safe="")
        interval = self._initial_s
        pending = True

        def cancel(reason: str) -> None:
            try:
                request_json(self._http, "POST", f"{self._url}/cancel/{job_url}",
                             endpoint_id=self._endpoint_id, headers=self._headers, timeout=self._request_timeout_s)
            except BenchmarkError as exc:
                raise BenchmarkError(f"{reason}; cancellation failed ({exc}); check the RunPod dashboard") from None

        try:
            while True:
                status = state.get("status")
                if not isinstance(status, str):
                    raise BenchmarkError("RunPod returned a missing or non-string job status")
                if status == "COMPLETED":
                    pending = False
                    if "output" not in state:
                        raise BenchmarkError("Completed RunPod job has no output")
                    worker = state.get("workerId")
                    if worker is not None and not isinstance(worker, str):
                        raise BenchmarkError("RunPod returned invalid workerId")
                    return JobResult(state["output"], job_id,
                                     optional_number(state.get("delayTime"), "delayTime"),
                                     optional_number(state.get("executionTime"), "executionTime"),
                                     self._clock() - started, worker)
                if status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                    pending = False
                    raise BenchmarkError(f"RunPod job ended with status {status}; inspect the endpoint dashboard")
                if status not in {"IN_QUEUE", "IN_PROGRESS"}:
                    raise BenchmarkError("RunPod returned an unknown job status")
                remaining = deadline - self._clock()
                if remaining <= 0:
                    pending = False
                    cancel("RunPod job timed out")
                    raise BenchmarkError("RunPod job timed out; cancellation requested")
                self._sleep(min(interval, remaining))
                remaining = deadline - self._clock()
                if remaining <= 0:
                    continue
                state = request_json(self._http, "GET", f"{self._url}/status/{job_url}",
                                     endpoint_id=self._endpoint_id, headers=self._headers,
                                     timeout=min(self._request_timeout_s, remaining))
                interval = min(interval * self._backoff, self._max_s)
        except KeyboardInterrupt:
            if pending:
                cancel("Run interrupted")
            raise
        except BenchmarkError:
            # Unknown remote state or a polling failure can leave a billable job running.
            if pending:
                cancel("RunPod monitoring failed")
            raise
