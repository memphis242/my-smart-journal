"""Diagnostic structure is actionable without arbitrary exception content."""

import errno
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from msj.api import EntryCreate
from msj.diagnostics import safe_exception


def test_untrusted_exception_filenames_and_validation_keys_are_redacted() -> None:
    private = "secret-journal-token"
    detail = safe_exception(OSError(errno.ENOENT, private, private))
    assert private not in json.dumps(detail)
    assert detail["errno"] == errno.ENOENT
    with pytest.raises(ValidationError) as error:
        EntryCreate.model_validate({"text": "synthetic", private: "private value"})
    detail = safe_exception(error.value)
    assert private not in json.dumps(detail)
    assert detail["validation"][0]["type"] == "extra_forbidden"


def test_trusted_resource_and_causal_evidence_are_preserved(tmp_path: Path) -> None:
    config = str(tmp_path / "config.toml")
    try:
        try:
            raise FileNotFoundError(errno.ENOENT, "private message", config)
        except FileNotFoundError as error:
            raise RuntimeError("private journal text") from error
    except RuntimeError as error:
        detail = safe_exception(error, trusted_paths=(config,))
    encoded = json.dumps(detail)
    assert "private" not in encoded
    assert detail["causes"][0]["filename"] == config
    assert detail["causes"][0]["errno"] == errno.ENOENT
    assert any("test_diagnostics.py:" in frame for frame in detail["frames"])
    db = sqlite3.connect(":memory:")
    try:
        with pytest.raises(sqlite3.OperationalError) as failure:
            db.execute("SELECT * FROM missing")
        assert safe_exception(failure.value)["sqlite_errorcode"] == sqlite3.SQLITE_ERROR
    finally:
        db.close()


def test_cli_missing_config_logs_known_path_and_errno(tmp_path: Path, monkeypatch, caplog) -> None:
    from msj.cli import main

    missing = tmp_path / "missing-config.toml"
    monkeypatch.setenv("MSJ_CONFIG_FILE", str(missing))
    assert main(["migrate"]) == 1
    assert str(missing) in caplog.text
    assert "'errno': 2" in caplog.text
    assert "FileNotFoundError" in caplog.text


def test_cli_invalid_lease_reports_configuration_field_and_rule(monkeypatch, caplog) -> None:
    from msj.cli import main

    monkeypatch.delenv("MSJ_CONFIG_FILE", raising=False)
    monkeypatch.setenv("MSJ_LEASE_S", "10")
    assert main(["migrate"]) == 1
    assert "lease_s" in caplog.text
    assert "lease_s_must_exceed_provider_timeout_s_plus_30" in caplog.text
    assert "'loc': ('lease_s',)" in caplog.text
