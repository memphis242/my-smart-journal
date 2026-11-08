"""HTTP regressions for failed-auth throttling and token migration behavior."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from msj import cli
from msj.api import create_app
from msj.auth import create_token
from msj.config import TASKS, EmbeddingSettings, Settings, TaskRoute


def _client(data_dir: Path, *, login_limit: int = 1) -> TestClient:
    settings = Settings(
        data_dir=data_dir,
        insecure_cookies=True,
        login_rate_limit=login_limit,
        embedding=EmbeddingSettings(provider="none"),
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    return TestClient(create_app(settings))


def test_bearer_failures_are_limited_across_protected_routes(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        create_token(client.app.state.store)
        responses = [
            client.get("/api/v1/queue", headers={"Authorization": "Bearer invalid"})
            for _ in range(10)
        ]
        second = client.get("/api/v1/session", headers={"Authorization": "Bearer invalid"})
        assert all(response.status_code == 401 for response in responses)
        assert second.status_code == 429
        assert second.json()["code"] == "RATE_LIMITED"
        healthy = client.get("/api/v1/health")
        assert healthy.status_code == 200
        assert healthy.json() == {"status": "ok"}


def test_valid_bearer_is_allowed_after_failures_fill_client_bucket(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        token = create_token(client.app.state.store)
        for _ in range(10):
            response = client.get("/api/v1/queue", headers={"Authorization": "Bearer invalid"})
            assert response.status_code == 401
        valid = client.get("/api/v1/queue", headers={"Authorization": f"Bearer {token}"})
        assert valid.status_code == 200


def test_forwarded_for_cannot_rotate_failed_auth_bucket(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        create_token(client.app.state.store)
        responses = [
            client.get(
                "/api/v1/queue",
                headers={
                    "Authorization": "Bearer invalid",
                    "X-Forwarded-For": f"192.0.2.{index + 1}",
                },
            )
            for index in range(10)
        ]
        second = client.get(
            "/api/v1/queue",
            headers={"Authorization": "Bearer invalid", "X-Forwarded-For": "198.51.100.7"},
        )
        assert all(response.status_code == 401 for response in responses)
        assert second.status_code == 429


def test_successful_logins_do_not_consume_failure_budget(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        token = create_token(client.app.state.store)
        for _ in range(3):
            response = client.post("/api/v1/session", json={"token": token})
            assert response.status_code == 204


def test_legacy_hash_returns_stable_reissue_code_from_both_routes(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        store = client.app.state.store
        token = create_token(store)
        store.set_token_hash("reissue-required")
        auth_response = client.get(
            "/api/v1/queue", headers={"Authorization": f"Bearer {token}"}
        )
        login_response = client.post("/api/v1/session", json={"token": token})
        for response in (auth_response, login_response):
            assert response.status_code == 401
            assert response.json()["code"] == "TOKEN_REISSUE_REQUIRED"
            assert "msj create-token" in response.json()["detail"]


def test_missing_token_secret_returns_actionable_reissue_error(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        token = create_token(client.app.state.store)
        (tmp_path / "token.secret").unlink()
        response = client.get("/api/v1/queue", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json()["code"] == "TOKEN_REISSUE_REQUIRED"
        assert "token.secret" in response.json()["detail"]
        assert "msj create-token" in response.json()["detail"]


def test_unsafe_token_secret_mode_returns_actionable_internal_error(tmp_path: Path, caplog) -> None:
    with _client(tmp_path) as client:
        token = create_token(client.app.state.store)
        secret_path = tmp_path / "token.secret"
        secret_path.chmod(0o644)
        response = client.get("/api/v1/queue", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 500
        assert response.json()["code"] == "TOKEN_SECRET_INVALID"
        assert "permissions to 0600" in response.json()["detail"]
        assert str(secret_path) in caplog.text


@pytest.mark.parametrize("key_state", ["mismatched", "missing", "legacy"])
def test_unusable_token_key_has_actionable_code_and_key_path_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, key_state: str,
) -> None:
    with _client(tmp_path) as client:
        store = client.app.state.store
        token = create_token(store)
        path = tmp_path / "token.secret"
        match key_state:
            case "missing":
                path.unlink()
            case "mismatched":
                _ = path.write_bytes(b"z" * 32)
            case "legacy":
                stored = store.token_hash()
                store.set_token_hash("hmac-sha256$" + stored.rsplit("$", 1)[1])
        for response in (
            client.get("/api/v1/queue", headers={"Authorization": f"Bearer {token}"}),
            client.post("/api/v1/session", json={"token": token}),
        ):
            assert response.status_code == 401
            assert response.json()["code"] == "TOKEN_REISSUE_REQUIRED"
            assert "msj create-token" in response.json()["detail"]
        assert str(path) in caplog.text


def test_queue_missing_batch_state_has_actionable_safe_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    with _client(tmp_path) as client:
        store = client.app.state.store
        token = create_token(store)
        with store.connect() as db:
            _ = db.execute("DELETE FROM batch_state WHERE id=1")
        response = client.get("/api/v1/queue", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 500
        assert response.json()["code"] == "BATCH_STATE_UNAVAILABLE"
        assert "msj migrate" in response.json()["detail"]
        assert "msj migrate" in caplog.text


def test_serve_passes_explicit_forwarded_ip_trust_setting(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(data_dir=tmp_path, forwarded_allow_ips="127.0.0.1,::1")
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_app", lambda _settings: object())

    def capture_run(_app: object, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", capture_run)
    assert cli.main(["serve", "--port", "18766"]) == 0
    assert captured["forwarded_allow_ips"] == "127.0.0.1,::1"
