"""Regression coverage for adversarial HTTP inputs and privacy boundaries."""

import asyncio
import json
import multiprocessing
import sqlite3
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from msj.api import create_app
from msj.auth import create_token
from msj.config import TASKS, Settings, TaskRoute
from msj.pipeline import Runner


def make_client(tmp_path: Path, **kwargs: Any) -> tuple[TestClient, dict[str, str]]:
    settings = Settings(
        data_dir=tmp_path / "data",
        embedding={"provider": "fake"},
        review={"enabled_for": ()},
        tasks={k: TaskRoute(provider="fake") for k in TASKS},
        **kwargs,
    )
    app = create_app(settings)
    return TestClient(app), {"Authorization": "Bearer " + create_token(app.state.store)}


@pytest.mark.parametrize("k", [True, 1.9, "2", None])
def test_retrieve_requires_strict_integer(tmp_path: Path, k: Any) -> None:
    client, auth = make_client(tmp_path)
    response = client.post("/api/v1/retrieve", headers=auth, json={"query": "launch", "k": k})
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_RETRIEVAL_REQUEST"


def test_static_assets_private_files_and_missing_chunks(tmp_path: Path) -> None:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    for name in [
        "index.html",
        "manifest.json",
        "settings.js",
        "manifest.webmanifest",
        ".env",
        "assets/app-AbCdEfGh.js",
    ]:
        (web / name).write_text("synthetic")
    client, _ = make_client(tmp_path, web_dir=web)
    for name in ["manifest.json", "settings.js", "manifest.webmanifest"]:
        assert "immutable" not in client.get("/" + name).headers.get("cache-control", "")
    assert "immutable" in client.get("/assets/app-AbCdEfGh.js").headers["cache-control"]
    for path in ["/.env", "/assets/.private", "/missing.js", "/assets/missing"]:
        assert client.get(path).status_code == 404
    assert client.get("/entry/a").status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/search", "/api/v1/retrieve"])
def test_control_char_query_rejected(tmp_path: Path, path: str) -> None:
    client, auth = make_client(tmp_path)
    response = (
        client.get(path, params={"q": "\x00"}, headers=auth)
        if path.endswith("search")
        else client.post(path, json={"query": "\x00"}, headers=auth)
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_QUERY"


def test_every_problem_has_code_and_private_responses_no_store(tmp_path: Path) -> None:
    client, auth = make_client(tmp_path)
    entry = client.post("/api/v1/entries", headers=auth, json={"text": "synthetic"}).json()
    paths = [
        "/api/v1/entries/missing",
        "/api/v1/entries?limit=nope",
        f"/api/v1/entries/{entry['id']}/neighbors?from=2024-13-01",
        "/api/v1/unknown",
    ]
    for path in paths:
        response = client.get(path, headers=auth)
        assert response.status_code in {404, 422}
        assert response.json()["code"]
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/entries", headers=auth).headers["cache-control"] == "no-store"
    exported = client.post(
        "/api/v1/export", headers=auth, json={"entry_ids": [entry["id"]], "format": "text"}
    )
    assert exported.headers["cache-control"] == "no-store"


def test_unexpected_value_error_is_private_logged_500(
    tmp_path: Path, monkeypatch: Any, caplog: Any
) -> None:
    client, auth = make_client(tmp_path)

    def fail() -> Any:
        raise ValueError("private journal token text")

    monkeypatch.setattr(client.app.state.store, "all_entries", fail)
    response = client.get("/api/v1/entries", headers=auth)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "private journal token text" not in response.text + caplog.text
    assert "/api/v1/entries" in caplog.text and "ValueError" in caplog.text
    assert "test_adversarial_api.py:" in caplog.text


@pytest.mark.parametrize("query", ["gardening", "product"])
def test_retrieve_uses_one_snapshot(tmp_path: Path, monkeypatch: Any, query: str) -> None:
    client, auth = make_client(tmp_path)
    store = client.app.state.store
    entry = store.create_entry(text="gardening plans")
    assert Runner(store, store.settings).run_batch()["failed"] == 0
    original = client.app.state.embedding.search

    def revise_between_reads(query: str, allowed_ids: Any = None, **kwargs: Any) -> Any:
        store.patch_entry(entry["id"], {"text": "product launch"})
        assert Runner(store, store.settings).run_batch()["failed"] == 0
        return original(query, allowed_ids=allowed_ids, **kwargs)

    monkeypatch.setattr(client.app.state.embedding, "search", revise_between_reads)
    response = client.post("/api/v1/retrieve", headers=auth, json={"query": query})
    assert response.status_code == 200, response.text
    if query == "product":
        assert response.json()["items"] == []
        return
    item = response.json()["items"][0]
    assert item["entry"]["revision"] == 1
    assert all(
        passage in item["entry"]["text"] or passage == item["entry"]["summary"]
        for passage in item["passages"]
    )


def test_ndjson_internal_error_has_code(tmp_path: Path, monkeypatch: Any) -> None:
    client, auth = make_client(tmp_path)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("private")

    monkeypatch.setattr(client.app.state.store, "fts_search", fail)
    response = client.get("/api/v1/search", params={"q": "launch"}, headers=auth)
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["error"]["code"] == "INTERNAL_ERROR"


def test_compilation_requires_nonempty_entry_ids(tmp_path: Path) -> None:
    client, auth = make_client(tmp_path)
    response = client.post(
        "/api/v1/compilations",
        headers=auth,
        json={"entry_ids": [], "purpose": "review", "period": "today"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_COMPILATION_REQUEST"


async def test_bearer_flood_is_bounded_and_other_client_remains_responsive(tmp_path: Path) -> None:
    client, auth = make_client(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app), base_url="http://testserver"
    ) as http:
        junk = await asyncio.gather(
            *(
                http.get("/api/v1/queue", headers={"Authorization": "Bearer junk"})
                for _ in range(100)
            )
        )
        assert any(response.status_code == 429 for response in junk)
        assert all(response.status_code in {401, 429} for response in junk)
        assert (await http.get("/api/v1/health")).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app, client=("192.0.2.1", 1000)),
        base_url="http://testserver",
    ) as other:
        assert (
            await asyncio.wait_for(other.get("/api/v1/queue", headers=auth), 2)
        ).status_code == 200


async def test_query_embedding_runs_off_the_event_loop(tmp_path: Path, monkeypatch: Any) -> None:
    client, auth = make_client(tmp_path)
    started, resume = threading.Event(), threading.Event()
    original = client.app.state.embedding.search

    def slow(*args: Any, **kwargs: Any) -> Any:
        started.set()
        assert resume.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(client.app.state.embedding, "search", slow)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app), base_url="http://testserver"
    ) as http:
        pending = asyncio.create_task(
            http.post("/api/v1/retrieve", headers=auth, json={"query": "launch"})
        )
        try:
            assert await asyncio.to_thread(started.wait, 3)
            assert (await asyncio.wait_for(http.get("/api/v1/health"), 1)).status_code == 200
        finally:
            resume.set()
            assert (await pending).status_code == 200


def test_batch_request_contract_is_durable_and_conflicts_with_active_lease(tmp_path: Path) -> None:
    client, auth = make_client(tmp_path)
    first = client.post("/api/v1/batches/run", headers=auth)
    assert first.status_code == 202
    assert first.json() == {"requested": True, "worker": {"stale": True}}
    assert client.post("/api/v1/batches/run", headers=auth).json() == first.json()
    coordinator = client.app.state.batch_coordinator
    assert not hasattr(client.app.state, "runner")
    batch_id = coordinator.acquire("manual")
    assert batch_id is not None
    blocked = client.post("/api/v1/batches/run", headers=auth)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "BATCH_ALREADY_RUNNING"
    coordinator.release(batch_id, None)


def _claim_lease_in_child(data_dir: Path) -> None:
    from msj.batch import BatchCoordinator
    from msj.storage import Store

    store = Store(
        Settings(
            data_dir=data_dir,
            embedding={"provider": "none"},
            tasks={k: TaskRoute(provider="fake") for k in TASKS},
        )
    )
    assert BatchCoordinator(store, Runner(store, store.settings)).acquire("manual") is not None


def test_http_batch_request_recovers_after_owner_process_exits(tmp_path: Path) -> None:
    client, auth = make_client(tmp_path)
    process = multiprocessing.get_context("spawn").Process(
        target=_claim_lease_in_child, args=(client.app.state.store.data_dir,)
    )
    process.start()
    try:
        process.join(timeout=10)
        assert not process.is_alive()
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    response = client.post("/api/v1/batches/run", headers=auth)
    assert response.status_code == 202
    assert response.json()["requested"] is True


def test_session_validation_is_read_only_during_a_writer(tmp_path: Path) -> None:
    from msj.web_auth import new_session, valid_session

    client, _ = make_client(tmp_path)
    store = client.app.state.store
    value = new_session(store, store.token_hash())
    started, finished = threading.Event(), threading.Event()
    result: list[bool] = []

    def validate() -> None:
        started.set()
        result.append(valid_session(store, value))
        finished.set()

    with sqlite3.connect(store.db_path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        reader = threading.Thread(target=validate)
        reader.start()
        try:
            assert started.wait(2)
            assert finished.wait(1), "session validation waited for the writer lock"
        finally:
            writer.rollback()
            reader.join(timeout=5)
    assert result == [True]


def test_failed_inline_batch_is_exposed_by_queue(tmp_path: Path, monkeypatch: Any) -> None:
    from msj.batch import BatchCoordinator

    client, auth = make_client(tmp_path)
    store = client.app.state.store

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("private journal text")

    monkeypatch.setattr(Runner, "run_batch", fail)
    with pytest.raises(RuntimeError):
        BatchCoordinator(store, Runner(store, store.settings)).run_sync()
    response = client.get("/api/v1/queue", headers=auth)
    assert response.json()["batch"]["last_error"]["code"] == "BATCH_FAILED"
    assert "private journal text" not in response.text


def test_server_owns_query_embeddings_and_leaves_batch_inference_to_worker(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    assert client.app.state.embedding is not None
    assert not hasattr(client.app.state, "runner")


def test_failed_audio_creation_keeps_a_durable_cleanup_obligation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, _ = make_client(tmp_path)
    store = client.app.state.store
    original_unlink = Path.unlink

    def queue_failure(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic queue failure")

    def unlink_failure(path: Path, *args: Any, **kwargs: Any) -> None:
        if path.suffix == ".wav":
            raise OSError("synthetic unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(store, "_queue", queue_failure)
    monkeypatch.setattr(Path, "unlink", unlink_failure)
    with pytest.raises(RuntimeError):
        store.create_entry(audio=b"synthetic audio", extension="wav")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM file_deletions").fetchone()[0] == 1
    monkeypatch.setattr(Path, "unlink", original_unlink)
    store.migrate()
    assert list((store.data_dir / "audio").rglob("*.wav")) == []
