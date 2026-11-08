"""Expensive export rendering leaves the API event loop responsive."""

import asyncio
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

from msj import exporting
from msj.api import create_app
from msj.auth import create_token
from msj.config import TASKS, Settings, TaskRoute


async def test_health_responds_while_export_is_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path,
            tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
        )
    )
    auth = {"Authorization": f"Bearer {create_token(app.state.store)}"}
    entry = app.state.store.create_entry(text="Synthetic export content.")
    started = threading.Event()
    resume = threading.Event()

    def slow_render(*args: Any, **kwargs: Any) -> tuple[bytes, str, str]:
        started.set()
        _ = resume.wait(2)
        return b"Synthetic export", "text/plain", "txt"

    monkeypatch.setattr(exporting, "render", slow_render)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        pending = asyncio.create_task(
            client.post(
                "/api/v1/export",
                headers=auth,
                json={"entry_ids": [entry["id"]], "format": "text"},
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert not pending.done()
            health = await asyncio.wait_for(client.get("/api/v1/health"), timeout=1)
            assert health.status_code == 200
        finally:
            resume.set()
            result = await pending
        assert result.status_code == 200
