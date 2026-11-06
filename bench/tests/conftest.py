"""All tests use HTTP MockTransport; a real socket connection is a test error."""

from pathlib import Path
import socket

import pytest

BUILD = Path("/workspace/build/my-smart-journal-phase0")


def pytest_configure(config):
    if config.option.basetemp is None and not hasattr(config, "workerinput"):
        config.option.basetemp = str(BUILD / "pytest-tmp")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject_connection(*args, **kwargs):
        raise AssertionError("Tests must not make live network connections")
    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    # Even orchestration tests must never load the worktree's private .env.
    monkeypatch.setattr("msj_bench.run.load_dotenv", lambda *args, **kwargs: False)


@pytest.fixture
def empty_extraction():
    return {"projects": [], "skills_technologies": [], "accomplishments": [],
            "people_roles": [], "star_fragments": [], "tags": []}
