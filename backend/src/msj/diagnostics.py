"""Structural exception evidence with no exception values or source excerpts."""

import sqlite3
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

# Only schema-owned names may reach diagnostic logs; arbitrary mapping keys are private.
_FIELDS = frozenset(
    """
body query path header cookie text title tags entry_date entry_ids purpose period format
include order limit offset from to from_date to_date tag project skill view status k token
query data_dir config_file web_dir insecure_cookies login_rate_limit login_rate_window_s
forwarded_allow_ips worker_tick_s bearer_rate_limit bearer_rate_window_s review max_turns
enabled_for embedding provider model revision device chunk_tokens chunk_overlap min_score
top_k tasks transcribe cleanup summarize extract compile expect_model endpoint_id base_url
max_tokens batch_interval_s max_attempts retry_base_s lease_s provider_timeout_s
poll_initial_s poll_max_s runpod_max_payload_bytes runpod_status_max_failures
upload_limit_bytes fuzzy_threshold
""".split()
)


def validation_issues(errors: Iterable[Any]) -> list[dict[str, Any]]:
    """Retain schema field names and positions while eliding private extra-field keys."""
    return [
        {
            "loc": tuple(
                part if isinstance(part, int) or part in _FIELDS else "<field>"
                for part in item["loc"]
            ),
            "type": item["type"],
        }
        for item in list(errors)[:20]
    ]


def safe_exception(exc: BaseException, *, trusted_paths: tuple[str, ...] = ()) -> dict[str, Any]:
    """Keep bounded causal evidence; filename values require an exact trusted resource."""

    def evidence(error: BaseException) -> dict[str, Any]:
        result: dict[str, Any] = {"type": type(error).__name__}
        frames: list[str] = []
        trace = error.__traceback__
        while trace is not None:
            frames.append(f"{trace.tb_frame.f_code.co_filename}:{trace.tb_lineno}")
            trace = trace.tb_next
        result["frames"] = frames[-12:]
        if isinstance(error, OSError):
            result["errno"] = error.errno
            if error.filename is not None:
                result["filename"] = (
                    error.filename if error.filename in trusted_paths else "<untrusted filename>"
                )
        if isinstance(error, sqlite3.Error):
            result["sqlite_errorcode"] = getattr(error, "sqlite_errorcode", None)
            result["sqlite_errorname"] = getattr(error, "sqlite_errorname", None)
        if isinstance(error, ValidationError):
            result["validation"] = validation_issues(
                error.errors(include_input=False, include_context=False)
            )
        return result

    result = evidence(exc)
    causes: list[dict[str, Any]] = []
    seen = {id(exc)}
    cause = exc.__cause__ or exc.__context__
    while cause is not None and id(cause) not in seen and len(causes) < 4:
        seen.add(id(cause))
        causes.append(evidence(cause))
        cause = cause.__cause__ or cause.__context__
    if causes:
        result["causes"] = causes
    return result
