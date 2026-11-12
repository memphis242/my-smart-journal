"""Reject real HTTP transports in tests; provider tests use MockTransport."""

import importlib
import os
from typing import Any

import httpx
import pytest
from hypothesis import settings
from hypothesis.database import DirectoryBasedExampleDatabase

# Hypothesis keeps failing examples in an example database, `.hypothesis/` in the working
# directory by default. Generated output stays out of the source tree: the database lives
# where MSJ_HYPOTHESIS_DB points (scripts/check.sh uses the build directory), and is
# disabled otherwise. Deadlines are off because xdist and CI timing make them flaky.
_EXAMPLE_DB = os.environ.get("MSJ_HYPOTHESIS_DB")
settings.register_profile(
    "dev",
    database=DirectoryBasedExampleDatabase(_EXAMPLE_DB) if _EXAMPLE_DB else None,
    deadline=None,
)
settings.register_profile("ci", parent=settings.get_profile("dev"), max_examples=300)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    # Each semantic test selects its fake embedder explicitly.
    monkeypatch.setenv("MSJ_EMBEDDING__PROVIDER", "none")

    if (
        os.environ.get("MSJ_TEST_REAL_EMBEDDER") == "1"
        and request.node.name == "test_real_bge_model_ranks_semantically_related_entry"
    ):
        return

    def reject(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Tests must use MockTransport or an in-process ASGI transport")

    if request.node.get_closest_marker("integration") is not None:
        # Process-level tests drive real `msj` subprocesses over loopback HTTP only.
        original = httpx.HTTPTransport.handle_request

        def loopback_only(transport: httpx.HTTPTransport, http_request: httpx.Request) -> Any:
            if http_request.url.host not in _LOOPBACK_HOSTS:
                raise AssertionError(
                    f"Integration tests may only reach loopback, not {http_request.url.host!r}"
                )
            return original(transport, http_request)

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", loopback_only)
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)
        return

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)
    try:
        optional_http = importlib.import_module("httpx2")
    except ModuleNotFoundError:
        return
    monkeypatch.setattr(optional_http.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(optional_http.AsyncHTTPTransport, "handle_async_request", reject)
