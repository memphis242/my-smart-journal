"""Configuration and private storage boundary checks."""

import stat
from pathlib import Path

import pytest

from msj.config import load_settings
from msj.storage import Store


@pytest.mark.parametrize("max_turns", [0, 4])
def test_review_turn_limit_is_bounded(max_turns: int) -> None:
    from pydantic import ValidationError

    from msj.config import ReviewSettings

    with pytest.raises(ValidationError):
        ReviewSettings(max_turns=max_turns)


def test_review_policy_environment_override_preserves_toml_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "review.toml"
    _ = config.write_text('[review]\nmax_turns=3\nenabled_for=["cleanup", "extract"]\n')
    monkeypatch.setenv("MSJ_CONFIG_FILE", str(config))
    monkeypatch.setenv("MSJ_REVIEW__MAX_TURNS", "2")
    settings = load_settings()
    assert settings.review.max_turns == 2
    assert settings.review.enabled_for == ("cleanup", "extract")


def test_nested_environment_overrides_preserve_toml_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.toml"
    _ = config.write_text("""[tasks.cleanup]
provider = "openai"
base_url = "http://local/v1"
model = "phi"
[tasks.review]
provider = "openai"
model = "gemma"
""")
    monkeypatch.setenv("MSJ_CONFIG_FILE", str(config))
    monkeypatch.setenv("MSJ_TASKS__CLEANUP__MODEL", "custom-phi")
    settings = load_settings()
    assert settings.tasks["cleanup"].provider == "openai"
    assert settings.tasks["cleanup"].model == "custom-phi"
    assert settings.tasks["review"].model == "gemma"
    assert len(settings.tasks) == 5


def test_existing_storage_directory_is_private(tmp_path: Path) -> None:
    from msj.config import Settings

    tmp_path.chmod(0o755)
    store = Store(Settings(data_dir=tmp_path))
    store.migrate()
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.db_path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "base_url", ["https://user:secret-token@local/v1", "https://local/v1?api_key=secret-token"]
)
def test_route_urls_cannot_embed_credentials(base_url: str) -> None:
    from pydantic import ValidationError

    from msj.config import TaskRoute

    with pytest.raises(ValidationError) as error:
        TaskRoute(provider="openai", base_url=base_url)
    assert "secret-token" not in str(error.value)
