"""Prompts and schema constraints shared by every model under comparison."""

import json
from pathlib import Path
from typing import Any, Final

from .backends import Message
from .datasets import BENCH_ROOT
from .runpod_client import BenchmarkError

TASK_NAMES: Final = ("cleanup", "summarize", "extract", "review", "compile")
REVIEW_SCHEMA: Final[dict[str, Any]] = {
    "type": "object", "additionalProperties": False,
    "properties": {"unsupported_claims": {"type": "array", "items": {"type": "string"}}},
    "required": ["unsupported_claims"],
}


def resource_path(kind: str, name: str) -> Path:
    local = BENCH_ROOT / kind / name
    return local if local.is_file() else Path(__file__).parent / "resources" / kind / name


def extraction_schema() -> dict[str, Any]:
    return json.loads(resource_path("schemas", "extraction.schema.json").read_text(encoding="utf-8"))


def compile_schema() -> dict[str, Any]:
    return json.loads(resource_path("schemas", "compile.schema.json").read_text(encoding="utf-8"))


def task_schema(task: str) -> dict[str, Any] | None:
    match task:
        case "extract":
            return extraction_schema()
        case "review":
            return dict(REVIEW_SCHEMA)
        case "compile":
            return compile_schema()
        case _:
            return None


def messages_for(task: str, source: str, *, candidate: str | None = None) -> list[Message]:
    if task not in TASK_NAMES:
        raise BenchmarkError(f"Unknown task: {task}")
    prompt = resource_path("prompts", f"{task}.txt").read_text(encoding="utf-8").strip()
    payload = {"source": source}
    if task == "review":
        if candidate is None:
            raise BenchmarkError("Review needs a candidate output")
        payload["candidate"] = candidate
    return [{"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
