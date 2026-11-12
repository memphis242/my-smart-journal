"""Bounded batch execution with schema validation and transactional publication."""

import json
import logging
from dataclasses import dataclass
from importlib.resources import files
from typing import TYPE_CHECKING, Any, cast

import httpx
from jsonschema import Draft202012Validator

from .config import Settings, TaskKind
from .diagnostics import safe_exception
from .providers import (
    InferenceProvider,
    Provider,
    ProviderError,
    SafeProviderError,
    prompt_digest,
    revision_messages,
)
from .shutdown import ShutdownRequested, StopSignal
from .storage import Store

if TYPE_CHECKING:
    from .embedding import EmbeddingService

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
    if kind not in {"extract", "review", "compile"}:
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
    elif kind == "compile":
        schema = json.loads(
            files("msj").joinpath("resources/compile.schema.json").read_text(encoding="utf-8")
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
    def __init__(
        self,
        store: Store,
        settings: Settings,
        provider: Provider | None = None,
        embedding: EmbeddingService | None = None,
        stop: StopSignal | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self._provider = provider
        self._embedding: EmbeddingService | None = embedding
        self._stop = stop

    def run_batch(self, *, _lease_id: str | None = None) -> dict[str, Any]:
        from .batch import BatchCoordinator

        if _lease_id is not None:
            return self._execute_batch(_lease_id)
        return BatchCoordinator(self.store, self).run_sync("manual")

    def _execute_batch(self, lease_id: str) -> dict[str, Any]:  # noqa: C901, PLR0912, PLR0915
        # Session follow-ups stay in the batch; new entry revisions wait for another pass.
        snapshot = {
            (job["entry_id"], job["revision"], job.get("compilation_id"))
            for job in self.store.list_jobs()
            if job["status"] in {"queued", "running"}
        }
        attempted: set[str] = set()
        stats = {"done": 0, "failed": 0, "discarded": 0}
        with httpx.Client() as http:
            provider = self._provider or InferenceProvider(self.settings, http, stop=self._stop)
            while True:
                if self._stop is not None and self._stop.is_set():
                    # Unclaimed jobs stay queued; the lease is released normally.
                    _logger.info("Batch ended early for shutdown; remaining jobs stay queued")
                    break
                eligible = {
                    job["id"]
                    for job in self.store.list_jobs()
                    if (job["entry_id"], job["revision"], job.get("compilation_id")) in snapshot
                    and job["status"] in {"queued", "running"}
                    and job["id"] not in attempted
                }
                from .batch import BatchCoordinator

                BatchCoordinator(self.store, self).renew(lease_id)
                job = self.store.claim(eligible, batch_id=lease_id)
                if job is None:
                    break
                attempted.add(job["id"])
                try:
                    if job["kind"] == "embed":
                        from .embedding import EmbeddingService

                        if self._embedding is None:
                            self._embedding = EmbeddingService(self.store, self.settings)
                        if self.settings.embedding.provider == "none":
                            with self.store.connect() as db:
                                self._embedding.reconcile(db, entry_ids={job["entry_id"]})
                            stats["discarded"] += 1
                            continue
                        try:
                            published = self._embedding.process(job)
                        except Exception as exc:  # noqa: BLE001 - recorded on the job; batch continues
                            diagnostic = safe_exception(exc)
                            _logger.error("Embedding job failed: %s", diagnostic)
                            from .embedding import EmbeddingFailure

                            cause: BaseException | None = exc
                            while cause is not None and not isinstance(cause, EmbeddingFailure):
                                cause = cause.__cause__
                            failure_code = (
                                cause.code
                                if isinstance(cause, EmbeddingFailure)
                                else "EMBEDDING_ENCODING_FAILED"
                            )
                            guidance = {
                                "EMBEDDING_DEPENDENCY_MISSING": (
                                    "install `my-smart-journal[semantic]` to process embedding jobs"
                                ),
                                "EMBEDDING_MODEL_LOAD_FAILED": (
                                    "verify embedding.model, embedding.revision, cache access, "
                                    "and model availability"
                                ),
                                "EMBEDDING_ENCODING_FAILED": (
                                    "verify model compatibility and vector dimensions"
                                ),
                            }[failure_code]
                            message = f"{failure_code}: {guidance}; see worker logs for details"
                            recorded = self.store.fail_job(job, message)
                            stats["failed" if recorded else "discarded"] += 1
                            continue
                        stats["done" if published else "discarded"] += 1
                        continue
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
                except ShutdownRequested:
                    # Not a failure: the job returns to the queue with its attempt restored,
                    # and the stop check above ends the batch, which releases the lease.
                    requeued = self.store.requeue_interrupted(job)
                    _logger.info(
                        "%s job %s interrupted by worker shutdown (%s)",
                        job["kind"],
                        job["id"],
                        "requeued" if requeued else "already reclaimed",
                    )
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
                except Exception as exc:  # noqa: BLE001 - one job's failure must not end the batch
                    _logger.error("Unexpected job exception: %s", safe_exception(exc))
                    recorded = self.store.fail_job(
                        job,
                        "Unexpected processing failure; retry the job and check provider "
                        "configuration and worker health",
                    )
                    stats["failed" if recorded else "discarded"] += 1
        return stats
