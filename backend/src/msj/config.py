"""Validated environment and optional TOML task routing configuration."""

import tomllib
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import EnvSettingsSource

TaskKind = Literal["transcribe", "cleanup", "review", "summarize", "extract", "compile"]
TASKS: tuple[TaskKind, ...] = ("transcribe", "cleanup", "review", "summarize", "extract", "compile")
ProducerKind = Literal["cleanup", "summarize", "extract"]
PRODUCERS: tuple[ProducerKind, ...] = ("cleanup", "summarize", "extract")


class ReviewSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    max_turns: int = Field(default=3, ge=1, le=3)
    enabled_for: tuple[ProducerKind, ...] = PRODUCERS

    @model_validator(mode="after")
    def unique_targets(self) -> ReviewSettings:
        if len(self.enabled_for) != len(set(self.enabled_for)):
            raise ValueError("review.enabled_for must not contain duplicate producers")
        return self


class EmbeddingSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    provider: Literal["sentence-transformers", "fake", "none"] = "sentence-transformers"
    model: str = Field(default="BAAI/bge-small-en-v1.5", min_length=1)
    revision: str = Field(default="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a", min_length=1)
    device: Literal["cpu"] = "cpu"
    chunk_tokens: int = Field(default=256, ge=1, le=4096)
    chunk_overlap: int = Field(default=32, ge=0, le=2048)
    min_score: float = Field(default=0.35, ge=-1, le=1)
    top_k: int = Field(default=20, ge=1, le=500)

    @model_validator(mode="after")
    def coherent_chunks(self) -> EmbeddingSettings:
        if self.chunk_overlap >= self.chunk_tokens:
            raise ValueError("embedding.chunk_overlap must be smaller than chunk_tokens")
        return self


class TaskRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    provider: Literal["fake", "runpod", "openai"] = "fake"
    model: str = Field(default="fake", min_length=1)
    expect_model: str | None = None
    endpoint_id: str | None = None
    base_url: str = "http://127.0.0.1:8080/v1"
    max_tokens: int = Field(default=4096, gt=0)

    @model_validator(mode="after")
    def endpoint_required(self) -> TaskRoute:
        if self.provider == "runpod" and not self.endpoint_id:
            raise ValueError("runpod routes require endpoint_id")
        try:
            address = urlsplit(self.base_url)
            valid = (
                address.scheme in {"http", "https"}
                and address.hostname is not None
                and address.username is None
                and address.password is None
                and not address.query
                and not address.fragment
            )
            _ = address.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "base_url must be an absolute HTTP(S) URL without credentials, "
                "query parameters, or fragments; set authentication via environment"
            )
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MSJ_",
        env_nested_delimiter="__",
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )
    data_dir: Path = Path("~/.local/share/my-smart-journal").expanduser()
    config_file: Path | None = None
    web_dir: Path | None = None
    insecure_cookies: bool = False
    forwarded_allow_ips: str = "127.0.0.1,::1"
    worker_tick_s: float = Field(default=2, gt=0, le=5)
    bearer_rate_limit: int = Field(default=10, ge=1)
    bearer_rate_window_s: float = Field(default=60, gt=0)
    login_rate_limit: int = Field(default=10, ge=1)
    login_rate_window_s: float = Field(default=60, gt=0)
    review: ReviewSettings = Field(default_factory=ReviewSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    tasks: Mapping[TaskKind, TaskRoute] = Field(
        default_factory=lambda: {
            kind: TaskRoute(
                provider="openai",
                model={"transcribe": "turbo", "cleanup": "Phi-4-mini"}.get(kind, "Gemma-4-26B-A4B"),
            )
            for kind in TASKS
        }
    )
    batch_interval_s: float = Field(default=86400, gt=0)
    max_attempts: int = Field(default=3, ge=1)
    retry_base_s: float = Field(default=30, gt=0)
    lease_s: float = Field(default=1200, gt=0)
    provider_timeout_s: float = Field(default=900, gt=0)
    poll_initial_s: float = Field(default=1, gt=0)
    poll_max_s: float = Field(default=10, gt=0)
    runpod_max_payload_bytes: int = Field(default=9_500_000, gt=0)
    runpod_status_max_failures: int = Field(default=3, ge=0)
    upload_limit_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    fuzzy_threshold: float = Field(default=80, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def complete_routes(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("tasks"), dict):
            defaults: dict[str, Any] = {
                kind: {
                    "provider": "openai",
                    "model": {
                        "transcribe": "turbo",
                        "cleanup": "Phi-4-mini",
                        "compile": "Gemma-4-26B-A4B",
                    }.get(kind, "Gemma-4-26B-A4B"),
                }
                for kind in TASKS
            }
            return value | {"tasks": _merge(defaults, value["tasks"])}
        return value

    @model_validator(mode="after")
    def coherent(self) -> Settings:
        if set(self.tasks) != set(TASKS):
            raise ValueError(
                "tasks must define transcribe, cleanup, review, summarize, extract, compile"
            )
        if self.lease_s <= self.provider_timeout_s + 30:
            raise ValidationError.from_exception_data(
                "Settings",
                [
                    {
                        "type": PydanticCustomError(
                            "lease_s_must_exceed_provider_timeout_s_plus_30",
                            "lease_s must exceed provider_timeout_s by more than 30 seconds",
                        ),
                        "loc": ("lease_s",),
                        "input": self.lease_s,
                    }
                ],
            )
        if self.poll_max_s < self.poll_initial_s:
            raise ValueError("poll_max_s must be >= poll_initial_s")
        object.__setattr__(self, "tasks", MappingProxyType(dict(self.tasks)))
        return self


def _merge(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overrides.items():
        previous = result.get(key)
        result[key] = (
            _merge(previous, value)
            if isinstance(previous, dict) and isinstance(value, dict)
            else value
        )
    return result


def load_settings() -> Settings:
    env = EnvSettingsSource(Settings)()
    config_path = env.get("config_file")
    document: dict[str, Any] = {}
    if config_path is not None:
        with Path(config_path).expanduser().open("rb") as stream:
            document = tomllib.load(stream)
    return Settings(**_merge(document, env))
