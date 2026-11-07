"""Typed configuration with no credential persistence."""

from dataclasses import dataclass
import math
from pathlib import Path
import tomllib
from typing import Any, Final
from urllib.parse import urlparse

from .datasets import BENCH_ROOT
from .runpod_client import BenchmarkError

DEFAULT_BUILD: Final = Path("/workspace/build/my-smart-journal-phase0")


@dataclass(frozen=True)
class Config:
    api_base: str
    stt_endpoint: str
    llm_endpoint: str
    stt_model: str
    llm_model: str
    hourly_usd: float
    timeout_s: float
    request_timeout_s: float
    poll_initial_s: float
    poll_max_s: float
    backoff: float
    max_tokens: int
    compile_max_tokens: int
    openai_base_url: str
    openai_model: str
    openai_timeout_s: float
    out_dir: Path


def _positive(table: dict[str, Any], key: str, default: float, *, zero: bool = False) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (value < 0 if zero else value <= 0):
        raise BenchmarkError(f"Config {key} must be a finite {'nonnegative' if zero else 'positive'} number")
    return float(value)


def _string(table: dict[str, Any], key: str, default: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"Config {key} must be a nonempty string")
    return value


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BenchmarkError("API base URL must use http(s), without credentials, query, or fragment")
    return value.rstrip("/")


def output_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.is_relative_to(BENCH_ROOT.parent):
        raise BenchmarkError("Generated outputs must be outside the worktree; choose --out-dir in the build directory")
    return resolved


def load_config(path: Path) -> Config:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BenchmarkError("Cannot read valid TOML configuration; check --config") from None
    tables = [data.get(name, {}) for name in ("runpod", "stt", "llm", "openai", "output")]
    if not all(isinstance(t, dict) for t in tables):
        raise BenchmarkError("Config sections must be TOML tables")
    runpod, stt, llm, openai, output = tables
    tokens = llm.get("max_tokens", 1024)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens <= 0:
        raise BenchmarkError("Config max_tokens must be a positive integer")
    compile_tokens = llm.get("compile_max_tokens", 3072)
    if isinstance(compile_tokens, bool) or not isinstance(compile_tokens, int) or compile_tokens <= 0:
        raise BenchmarkError("Config compile_max_tokens must be a positive integer")
    initial = _positive(runpod, "poll_initial_s", 1)
    maximum = _positive(runpod, "poll_max_s", 10)
    backoff = _positive(runpod, "backoff", 1.5)
    if maximum < initial or backoff < 1:
        raise BenchmarkError("Config poll_max_s must be >= poll_initial_s and backoff >= 1")
    return Config(
        validate_url(_string(runpod, "api_base", "https://api.runpod.ai/v2")),
        _string(stt, "endpoint_id", "b92qs7e2v6wi3n"), _string(llm, "endpoint_id", "rcybvy10rvnu2q"),
        _string(stt, "model", "turbo"), _string(llm, "model", "any"),
        _positive(runpod, "gpu_hourly_usd", 0.58, zero=True),
        _positive(runpod, "timeout_s", 900), _positive(runpod, "request_timeout_s", 30),
        initial, maximum, backoff, tokens, compile_tokens,
        validate_url(_string(openai, "base_url", "http://127.0.0.1:8080/v1")),
        _string(openai, "model", "local"), _positive(openai, "timeout_s", 300),
        output_root(Path(_string(output, "out_dir", str(DEFAULT_BUILD / "results")))),
    )
