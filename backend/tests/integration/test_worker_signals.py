"""`msj worker` shuts down gracefully on SIGTERM."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ROUTES = "\n".join(
    f'[tasks.{kind}]\nprovider = "fake"\nmodel = "fake"\n'
    for kind in ("transcribe", "cleanup", "review", "summarize", "extract", "compile")
)


def _environment(tmp_path: Path, embedding_provider: str) -> dict[str, str]:
    config = tmp_path / "config.toml"
    _ = config.write_text(
        "batch_interval_s = 600\nworker_tick_s = 0.2\n"
        f'[embedding]\nprovider = "{embedding_provider}"\n' + _ROUTES,
        encoding="utf-8",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"RUNPOD_API_KEY", "OPENAI_API_KEY"} and not key.startswith("MSJ_")
    }
    environment |= {
        "MSJ_CONFIG_FILE": str(config),
        "MSJ_DATA_DIR": str(tmp_path / "data"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
    }
    return environment


def _seed_entry(environment: dict[str, str]) -> None:
    script = (
        "from msj.config import load_settings\n"
        "from msj.storage import Store\n"
        "store = Store(load_settings())\n"
        "store.migrate()\n"
        "store.create_entry(text='I tuned the Atlas database index today.')\n"
    )
    _ = subprocess.run([sys.executable, "-c", script], env=environment, check=True, timeout=60)


def _stop_after_first_batch(tmp_path: Path, environment: dict[str, str]) -> tuple[int, str]:
    log_path = tmp_path / "worker.log"
    with log_path.open("wb") as log:
        worker = subprocess.Popen(
            [sys.executable, "-m", "msj", "worker"],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 120
            while b"Batch finished" not in log_path.read_bytes():
                assert worker.poll() is None, log_path.read_text(errors="replace")[-2000:]
                assert time.monotonic() < deadline, log_path.read_text(errors="replace")[-2000:]
                time.sleep(0.1)
            worker.send_signal(signal.SIGTERM)
            returncode = worker.wait(timeout=30)
        finally:
            if worker.poll() is None:
                worker.kill()
                _ = worker.wait(timeout=10)
    return returncode, log_path.read_text(errors="replace")


def test_worker_exits_cleanly_on_sigterm(tmp_path: Path) -> None:
    environment = _environment(tmp_path, "fake")
    _seed_entry(environment)
    returncode, log = _stop_after_first_batch(tmp_path, environment)
    assert returncode == 0, log[-2000:]
    assert "SIGTERM received" in log
    assert "Worker stopped" in log


@pytest.mark.skipif(
    os.environ.get("MSJ_TEST_REAL_EMBEDDER") != "1",
    reason="opt-in: needs sentence-transformers and the cached embedding model",
)
def test_worker_with_loaded_model_releases_semaphores_on_sigterm(tmp_path: Path) -> None:
    environment = _environment(tmp_path, "sentence-transformers")
    _seed_entry(environment)
    returncode, log = _stop_after_first_batch(tmp_path, environment)
    assert returncode == 0, log[-2000:]
    assert "leaked semaphore" not in log
