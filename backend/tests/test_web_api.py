"""Browser session and view contract tests."""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from msj.api import create_app
from msj.auth import create_token
from msj.config import TASKS, EmbeddingSettings, Settings, TaskRoute


def _client(
    data: Path,
    web: Path | None = None,
    *,
    login_rate_limit: int = 10,
    insecure_cookies: bool = True,
) -> tuple[TestClient, str]:
    settings = Settings(
        data_dir=data,
        web_dir=web,
        insecure_cookies=insecure_cookies,
        login_rate_limit=login_rate_limit,
        embedding=EmbeddingSettings(provider="fake"),
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    app = create_app(settings)
    token = create_token(app.state.store)
    base_url = "https://testserver" if not insecure_cookies else "http://testserver"
    return TestClient(app, base_url=base_url), token


def test_session_cookie_and_csrf(tmp_path: Path) -> None:
    client, token = _client(tmp_path)
    with client as http:
        bad = http.post("/api/v1/session", json={"token": "invalid"})
        assert bad.status_code == 401 and bad.json()["code"] == "BAD_TOKEN"
        assert http.post("/api/v1/session", json={"token": token}).status_code == 204
        cookie = http.cookies.get("msj_session")
        assert cookie is not None and token not in cookie
        set_cookie = http.cookies.jar
        assert any(
            item.name == "msj_session" and item.has_nonstandard_attr("HttpOnly")
            for item in set_cookie
        )
        assert http.get("/api/v1/session").status_code == 204
        rejected = http.post("/api/v1/entries", json={"text": "browser note"})
        assert rejected.status_code == 403
        assert rejected.json()["code"] == "CSRF_HEADER_MISSING"
        created = http.post(
            "/api/v1/entries", headers={"X-MSJ-CSRF": "1"}, json={"text": "browser note"}
        )
        assert created.status_code == 201
        assert http.delete("/api/v1/session").status_code == 204
        assert http.get("/api/v1/session").status_code == 401


def test_entry_filter_facets_neighbors_and_export(tmp_path: Path) -> None:
    client, token = _client(tmp_path)
    auth = {"Authorization": f"Bearer {token}"}
    with client as http:
        first = http.post(
            "/api/v1/entries",
            headers=auth,
            json={
                "text": "Atlas release notes",
                "title": "Release",
                "tags": ["Work"],
                "entry_date": "2026-11-06",
            },
        ).json()
        second = http.post(
            "/api/v1/entries",
            headers=auth,
            json={"text": "Hiring interview", "tags": ["Personal"], "entry_date": "2026-11-07"},
        ).json()
        response = http.get("/api/v1/entries", headers=auth, params=[("tag", "work")])
        assert response.status_code == 200
        assert response.json()["total"] == 1
        item = response.json()["items"][0]
        assert item["id"] == first["id"]
        assert {"summary", "text", "projects", "skills", "status", "revision"} <= item.keys()
        facets = http.get("/api/v1/facets", headers=auth).json()
        assert {item["value"] for item in facets["tags"]} == {"Work", "Personal"}
        neighbors = http.get(f"/api/v1/entries/{first['id']}/neighbors", headers=auth).json()
        assert neighbors["newer"] == second["id"]
        export = http.post(
            "/api/v1/export",
            headers=auth,
            json={
                "entry_ids": [first["id"]],
                "format": "text",
                "include": {
                    "title": True,
                    "date": True,
                    "tags": True,
                    "summary": True,
                    "text": True,
                },
            },
        )
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/plain")
        assert "2026-11-06  Release" in export.text
        assert "Atlas release notes" in export.text


def test_spa_fallback_security_headers(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("shell", encoding="utf-8")
    (web / "app-D6ab1cDe.js").write_text("asset", encoding="utf-8")
    (web / "app-AbCdEfGh.js").write_text("asset", encoding="utf-8")
    (web / "app.deadbeef.js").write_text("asset", encoding="utf-8")
    (web / "logo-background.svg").write_text("image", encoding="utf-8")
    outside = tmp_path / "outside.js"
    outside.write_text("private", encoding="utf-8")
    (web / "escape.js").symlink_to(outside)
    client, _ = _client(tmp_path / "data", web)
    with client as http:
        response = http.get("/some/deep/link")
        assert response.status_code == 200 and response.text == "shell"
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        asset = http.get("/app-D6ab1cDe.js")
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert (
            http.get("/app-AbCdEfGh.js").headers["cache-control"] == asset.headers["cache-control"]
        )
        assert (
            http.get("/app.deadbeef.js").headers["cache-control"] == asset.headers["cache-control"]
        )
        assert "cache-control" not in http.get("/logo-background.svg").headers
        assert http.get("/escape.js").status_code == 404
        assert http.get("/api/no-such-route").status_code == 404


def test_login_rate_limit_and_token_rotation(tmp_path: Path) -> None:
    client, token = _client(tmp_path, login_rate_limit=1)
    with client as http:
        rejected = http.post("/api/v1/session", json={"token": "invalid"})
        assert rejected.status_code == 401
        limited = http.post("/api/v1/session", json={"token": token})
        assert limited.status_code == 429
        assert limited.json()["code"] == "RATE_LIMITED"
        assert int(limited.headers["retry-after"]) > 0

    client, token = _client(tmp_path / "rotation")
    with client as http:
        assert http.post("/api/v1/session", json={"token": token}).status_code == 204
        create_token(http.app.state.store)
        assert http.get("/api/v1/session").status_code == 401


def test_secure_cookie_expiry_and_stale_token_snapshot(tmp_path: Path) -> None:
    from msj.web_auth import TokenChanged, new_session

    client, token = _client(tmp_path, insecure_cookies=False)
    with client as http:
        assert http.post("/api/v1/session", json={"token": token}).status_code == 204
        cookie = next(item for item in http.cookies.jar if item.name == "msj_session")
        assert cookie.secure is True
        assert cookie.has_nonstandard_attr("HttpOnly")
        cookie_value = http.cookies.get("msj_session")
        with http.app.state.store.connect() as db:
            session_hash = db.execute("SELECT session_hash FROM sessions").fetchone()[0]
        import hashlib

        assert session_hash == hashlib.sha256(cookie_value.encode()).hexdigest()
        with http.app.state.store.connect() as db:
            _ = db.execute("UPDATE sessions SET expires_at=?", (time.time() - 1,))
        assert http.get("/api/v1/session").status_code == 401
        digest_before_rotation = http.app.state.store.token_hash()
        create_token(http.app.state.store)
        assert http.get("/api/v1/session").status_code == 401
        try:
            new_session(http.app.state.store, digest_before_rotation)
        except TokenChanged:
            pass
        else:
            raise AssertionError("A stale token digest must not create a session")
        assert cookie_value is not None


def test_export_includes_nested_extraction_without_markdown_in_text() -> None:
    from msj.exporting import render

    item = {
        "id": "entry-1",
        "entry_date": "2026-11-08",
        "created_at": "2026-11-08T10:00:00+00:00",
        "title": "Launch",
        "tags": ["work"],
        "summary": "Released the service",
        "text": "The release is live.",
        "extraction": {
            "accomplishments": [{"description": "Improved startup", "metrics": ["12 s to 4 s"]}]
        },
    }
    flags = {
        "title": True,
        "date": True,
        "tags": True,
        "summary": True,
        "text": True,
        "extraction": True,
    }
    text_body, _, _ = render([item], format="text", include=flags, order="desc")
    rendered = text_body.decode()
    assert "description: Improved startup" in rendered
    assert "12 s to 4 s" in rendered
    assert "#" not in rendered and "- " not in rendered
    json_body, _, _ = render([item], format="json", include=flags, order="desc")
    assert '"metrics": [' in json_body.decode()


def test_retrieval_filters_and_fallback_ranking(tmp_path: Path) -> None:
    client, token = _client(tmp_path)
    auth = {"Authorization": f"Bearer {token}"}
    with client as http:
        entry = http.post(
            "/api/v1/entries", headers=auth, json={"text": "Atlas migration shipped"}
        ).json()
        invalid = http.post(
            "/api/v1/retrieve",
            headers=auth,
            json={"query": "Atlas", "filters": {"tag": "work"}},
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "INVALID_RETRIEVAL_FILTER"
        retrieved = http.post("/api/v1/retrieve", headers=auth, json={"query": "Atlas", "k": 5})
        assert retrieved.status_code == 200
        assert retrieved.json()["items"][0]["entry"]["id"] == entry["id"]
        assert retrieved.json()["unavailable"]["code"] == "EMBEDDINGS_PENDING"


def test_final_extraction_gates_facets_filters_and_exports(tmp_path: Path) -> None:
    client, token = _client(tmp_path)
    auth = {"Authorization": f"Bearer {token}"}
    with client as http:
        entry = http.post(
            "/api/v1/entries",
            headers=auth,
            json={"text": "Atlas project work", "tags": ["Work", "work"]},
        ).json()
        store = http.app.state.store
        with store.connect() as db:
            artifact_id = store._artifact(
                db,
                entry["id"],
                entry["revision"],
                "extraction",
                json.dumps(
                    {
                        "projects": ["Atlas"],
                        "skills_technologies": ["Python"],
                        "accomplishments": [
                            {"description": "Built release", "metrics": ["2 days"]}
                        ],
                        "people_roles": [],
                        "star_fragments": [],
                        "tags": [],
                    }
                ),
                target="extract",
            )
            _ = db.execute(
                "UPDATE review_sessions SET candidate_artifact_id=?,final_artifact_id=?,"
                "status='pending' "
                "WHERE entry_id=? AND revision=? AND target='extract'",
                (artifact_id, artifact_id, entry["id"], entry["revision"]),
            )
        pending = http.get("/api/v1/entries", headers=auth).json()["items"][0]
        assert pending["projects"] == [] and pending["skills"] == []
        assert http.get("/api/v1/facets", headers=auth).json()["projects"] == []
        with store.connect() as db:
            _ = db.execute(
                "UPDATE review_sessions SET status='accepted' WHERE entry_id=? "
                "AND target='extract'",
                (entry["id"],),
            )
        filtered = http.get(
            "/api/v1/entries",
            headers=auth,
            params=[("tag", "personal"), ("tag", "WORK"), ("project", "atlas")],
        ).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["projects"] == ["Atlas"]
        assert http.get("/api/v1/facets", headers=auth).json()["projects"] == [
            {"value": "Atlas", "count": 1}
        ]
        text_export = http.post(
            "/api/v1/export",
            headers=auth,
            json={"entry_ids": [entry["id"]], "format": "text", "include": {"extraction": True}},
        ).text
        assert "metrics:" in text_export and "2 days" in text_export
        json_export = http.post(
            "/api/v1/export",
            headers=auth,
            json={
                "entry_ids": [entry["id"]],
                "format": "json",
                "include": {"date": False, "title": False, "extraction": True},
            },
        ).json()[0]
        assert "entry_date" not in json_export and json_export["extraction"]["projects"] == [
            "Atlas"
        ]
        markdown_export = http.post(
            "/api/v1/export",
            headers=auth,
            json={
                "entry_ids": [entry["id"]],
                "format": "markdown",
                "include": {"date": False, "title": False, "text": False, "extraction": True},
            },
        ).text
        assert "Atlas" in markdown_export and "202" not in markdown_export
        missing = http.post(
            "/api/v1/export", headers=auth, json={"entry_ids": ["missing"], "format": "json"}
        )
        assert missing.status_code == 404 and missing.json()["code"] == "ENTRY_NOT_FOUND"
        too_many = http.post(
            "/api/v1/export",
            headers=auth,
            json={"entry_ids": [str(index) for index in range(1001)], "format": "json"},
        )
        assert too_many.status_code == 422
