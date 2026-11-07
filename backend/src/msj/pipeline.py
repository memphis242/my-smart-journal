"""Bounded batch execution with schema validation and transactional publication."""

import hashlib
import json
import logging
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Protocol

import httpx
from jsonschema import Draft202012Validator

from .config import Settings, TaskKind, TaskRoute
from .providers import Generation, InferenceProvider, ProviderError
from .storage import Store

_logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class ValidationResult:
    needs_attention: bool = False


def validate_output(kind: str, content: str) -> ValidationResult:
    if not content.strip():
        raise ProviderError("Inference returned empty text; check model and token limit")
    if kind not in {"extract", "review"}:
        return ValidationResult()
    try:
        data = json.loads(content)
    except ValueError, RecursionError:
        raise ProviderError(
            f"{kind} returned invalid JSON at $; retry with a schema-capable model"
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
        raise ProviderError(
            f"{kind} validation failed at {path}: schema {error.validator} "
            "constraint violated; check the configured model"
        )
    return ValidationResult(kind == "review" and bool(data["unsupported_claims"]))


class Runner:
    def __init__(self, store: Store, settings: Settings, provider: Provider | None = None) -> None:
        self.store = store
        self.settings = settings
        self._provider = provider

    def run_batch(self) -> dict[str, Any]:
        # A snapshot bounds the batch and prevents hot retries from looping indefinitely.
        eligible = {
            job["id"] for job in self.store.list_jobs() if job["status"] in {"queued", "running"}
        }
        stats = {"done": 0, "failed": 0, "discarded": 0}
        with httpx.Client() as http:
            provider = self._provider or InferenceProvider(self.settings, http)
            while (job := self.store.claim(eligible)) is not None:
                eligible.remove(job["id"])
                try:
                    source, candidate, audio = self.store.job_inputs(job)
                    kind = job["kind"]
                    route = self.settings.tasks[kind]
                    output = provider.generate(
                        kind, route, source, candidate=candidate, audio=audio
                    )
                    validation = validate_output(kind, output.text)
                    digest = (
                        None
                        if kind == "transcribe"
                        else hashlib.sha256(
                            files("msj").joinpath(f"resources/{kind}.txt").read_bytes()
                        ).hexdigest()
                    )
                    published = self.store.finish_job(
                        job, output.text, output.model, digest, validation.needs_attention
                    )
                    stats["done" if published else "discarded"] += 1
                except (ProviderError, OSError, KeyError) as exc:
                    message = (
                        str(exc)
                        if isinstance(exc, ProviderError)
                        else (
                            "Cannot read job input; check entry artifacts "
                            "and data directory permissions"
                        )
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
