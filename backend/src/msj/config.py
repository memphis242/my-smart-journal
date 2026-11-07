"""Validated environment and optional TOML task routing configuration."""

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import EnvSettingsSource

TaskKind = Literal["transcribe", "cleanup", "review", "summarize", "extract"]
TASKS: tuple[TaskKind, ...] = ("transcribe", "cleanup", "review", "summarize", "extract")


class TaskRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
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
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MSJ_",
        env_nested_delimiter="__",
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )
    data_dir: Path = Path("~/.local/share/my-smart-journal").expanduser()
    config_file: Path | None = None
    tasks: dict[TaskKind, TaskRoute] = Field(
        default_factory=lambda: {
            kind: TaskRoute(
                provider="openai",
                model={"transcribe": "turbo", "cleanup": "Phi-4-mini"}.get(kind, "Gemma-4-E2B"),
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
    upload_limit_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    fuzzy_threshold: float = Field(default=80, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def complete_routes(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("tasks"), dict):
            defaults: dict[str, Any] = {
                kind: {
                    "provider": "openai",
                    "model": {"transcribe": "turbo", "cleanup": "Phi-4-mini"}.get(
                        kind, "Gemma-4-E2B"
                    ),
                }
                for kind in TASKS
            }
            return value | {"tasks": _merge(defaults, value["tasks"])}
        return value

    @model_validator(mode="after")
    def coherent(self) -> Settings:
        if set(self.tasks) != set(TASKS):
            raise ValueError("tasks must define transcribe, cleanup, review, summarize, extract")
        if self.lease_s <= self.provider_timeout_s + 30:
            raise ValueError("lease_s must exceed provider_timeout_s by more than 30 seconds")
        if self.poll_max_s < self.poll_initial_s:
            raise ValueError("poll_max_s must be >= poll_initial_s")
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
