"""Bounded batch execution with schema validation and transactional publication."""

import json
import logging
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, cast

import httpx
from jsonschema import Draft202012Validator

from .config import Settings, TaskKind
from .providers import (
    InferenceProvider,
    Provider,
    ProviderError,
    SafeProviderError,
    prompt_digest,
    revision_messages,
)
from .storage import Store

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    unsupported_claims: tuple[str, ...] = ()

    @property
    def needs_attention(self) -> bool:
        return bool(self.unsupported_claims)


def validate_output(kind: str, content: str, *, retryable: bool = True) -> ValidationResult:
    if not content.strip():
        raise SafeProviderError(
            "Inference returned empty text; check model and token limit", retryable=retryable
        )
    if kind not in {"extract", "review"}:
        return ValidationResult()
    try:
        data = json.loads(content)
    except ValueError, RecursionError:
        raise SafeProviderError(
            f"{kind} returned invalid JSON at $; retry with a schema-capable model",
            retryable=retryable,
        ) from None
    schema: dict[str, Any]
    if kind == "extract":
        schema = json.loads(
            files("msj").joinpath("resources/extraction.schema.json").read_text(encoding="utf-8")
        )
    else:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"unsupported_claims": {"type": "array", "items": {"type": "string"}}},
            "required": ["unsupported_claims"],
        }
    error = next(iter(Draft202012Validator(schema).iter_errors(data)), None)
    if error is not None:
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
        )
        # Avoid storing provider output or journal text in job errors.
        raise SafeProviderError(
            f"{kind} validation failed at {path}: schema {error.validator} "
            "constraint violated; check the configured model",
            retryable=retryable,
        )
    return ValidationResult(tuple(data["unsupported_claims"]) if kind == "review" else ())


class Runner:
    def __init__(self, store: Store, settings: Settings, provider: Provider | None = None) -> None:
        self.store = store
        self.settings = settings
        self._provider = provider

    def run_batch(self) -> dict[str, Any]:
        # Session follow-ups stay in the batch; new entry revisions wait for another pass.
        snapshot = {
            (job["entry_id"], job["revision"])
            for job in self.store.list_jobs()
            if job["status"] in {"queued", "running"}
        }
        attempted: set[str] = set()
        stats = {"done": 0, "failed": 0, "discarded": 0}
        with httpx.Client() as http:
            provider = self._provider or InferenceProvider(self.settings, http)
            while True:
                eligible = {
                    job["id"]
                    for job in self.store.list_jobs()
                    if (job["entry_id"], job["revision"]) in snapshot
                    and job["status"] in {"queued", "running"}
                    and job["id"] not in attempted
                }
                job = self.store.claim(eligible)
                if job is None:
                    break
                attempted.add(job["id"])
                try:
                    source, candidate, audio = self.store.job_inputs(job)
                    kind = cast(TaskKind, job["target"] if job["kind"] == "revise" else job["kind"])
                    route = self.store.job_route(job)
                    digest = (
                        None
                        if kind == "transcribe"
                        else prompt_digest(kind, revision=job["kind"] == "revise")
                    )
                    if job["kind"] == "revise":
                        assert candidate is not None
                        messages = revision_messages(
                            kind, source, candidate, self.store.revision_claims(job)
                        )
                        output = provider.generate(kind, route, source, messages=messages)
                    else:
                        output = provider.generate(
                            kind, route, source, candidate=candidate, audio=audio
                        )
                    validation = validate_output(
                        kind, output.text, retryable=job["kind"] != "revise"
                    )
                    published = self.store.finish_job(
                        job,
                        output.text,
                        output.model,
                        digest,
                        unsupported_claims=validation.unsupported_claims,
                        producer_route=route,
                    )
                    stats["done" if published else "discarded"] += 1
                except (ProviderError, OSError, KeyError) as exc:
                    match exc:
                        case SafeProviderError():
                            message = str(exc)
                        case ProviderError():
                            message = (
                                "Inference failed; check provider configuration and retry the job"
                            )
                        case _:
                            message = (
                                "Cannot read job input; check entry artifacts "
                                "and data directory permissions"
                            )
                    recorded = self.store.fail_job(
                        job,
                        message,
                        retryable=exc.retryable if isinstance(exc, ProviderError) else True,
                    )
                    stats["failed" if recorded else "discarded"] += 1
                except Exception as exc:
                    _logger.error("Unexpected job exception: %s", type(exc).__name__)
                    recorded = self.store.fail_job(
                        job,
                        "Unexpected processing failure; retry the job and check provider "
                        "configuration and worker health",
                    )
                    stats["failed" if recorded else "discarded"] += 1
        return stats
