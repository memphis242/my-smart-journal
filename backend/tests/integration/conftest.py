"""Fixtures for process-level integration tests.

The harness itself lives in `msj_harness.py` so test modules can import its types without
importing a `conftest` module (several directories in this suite have one).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from .msj_harness import MsjFactory, MsjInstance, msj_instances


@pytest.fixture
def msj_factory(tmp_path: Path) -> Iterator[MsjFactory]:
    """Create isolated instances (own data dir and config) with overridable config."""
    with msj_instances(tmp_path) as make:
        yield make


@pytest.fixture
def msj(msj_factory: MsjFactory) -> MsjInstance:
    """A booted all-fake instance: migrated, token issued, server and idle worker running."""
    return msj_factory().boot()
