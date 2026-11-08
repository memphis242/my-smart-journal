"""Reject real HTTP transports in tests; provider tests use MockTransport."""

import importlib
import os
from typing import Any

import httpx
import pytest


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

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)
    try:
        optional_http = importlib.import_module("httpx2")
    except ModuleNotFoundError:
        return
    monkeypatch.setattr(optional_http.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(optional_http.AsyncHTTPTransport, "handle_async_request", reject)
