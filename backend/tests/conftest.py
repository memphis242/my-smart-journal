"""Reject real HTTP transports in tests; provider tests use MockTransport."""

from typing import Any

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Tests must use MockTransport or an in-process ASGI transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)
