"""Configuration and private storage boundary checks."""

import stat
from pathlib import Path

import pytest

from msj.config import load_settings
from msj.storage import Store


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
