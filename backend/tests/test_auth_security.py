"""Bearer token and browser-session security regressions."""

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from msj.auth import TokenReissueRequired, create_token, verify_token
from msj.config import TASKS, EmbeddingSettings, Settings, TaskRoute
from msj.storage import Store
from msj.web_auth import LoginLimiter, client_limit_key, new_session, valid_session


def _store(path: Path) -> Store:
    settings = Settings(
        data_dir=path,
        embedding=EmbeddingSettings(provider="none"),
        tasks={kind: TaskRoute(provider="fake") for kind in TASKS},
    )
    store = Store(settings)
    store.migrate()
    return store


def test_tokens_use_private_hmac_secret_and_reject_legacy_digest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    token = create_token(store)
    secret_path = tmp_path / "token.secret"
    assert secret_path.stat().st_mode & 0o777 == 0o600
    assert len(secret_path.read_bytes()) == 32
    token_hash = store.token_hash()
    assert token_hash is not None
    assert token_hash.startswith("hmac-sha256$")
    assert len((store.token_hash() or "").split("$")) == 3
    assert verify_token(store, token)
    assert not verify_token(store, "invalid")
    store.set_token_hash("scrypt$legacy$hash")
    with pytest.raises(TokenReissueRequired, match="msj create-token"):
        verify_token(store, token)
    store.set_token_hash("reissue-required")
    with pytest.raises(TokenReissueRequired, match="msj create-token"):
        verify_token(store, token)


def test_concurrent_first_use_publishes_one_complete_secret(tmp_path: Path) -> None:
    store = _store(tmp_path)
    barrier = threading.Barrier(16)
    tokens: list[str] = []
    errors: list[BaseException] = []

    def load_key() -> None:
        try:
            barrier.wait(timeout=5)
            tokens.append(create_token(store))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=load_key) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not errors
    assert len(tokens) == 16
    assert len(set(tokens)) == 16
    secret_path = tmp_path / "token.secret"
    assert len(secret_path.read_bytes()) == 32
    assert secret_path.stat().st_mode & 0o777 == 0o600
    assert any(verify_token(store, token) for token in tokens)


@pytest.mark.parametrize(
    ("contents", "mode", "expected"),
    [(b"short", 0o600, "exactly 32 bytes"), (b"x" * 32, 0o644, "permissions to 0600")],
)
def test_existing_secret_must_have_private_mode_and_expected_length(
    tmp_path: Path, contents: bytes, mode: int, expected: str
) -> None:
    from msj.auth import TokenSecretError, token_secret

    store = _store(tmp_path)
    secret_path = tmp_path / "token.secret"
    secret_path.write_bytes(contents)
    secret_path.chmod(mode)
    with pytest.raises(TokenSecretError, match=expected):
        token_secret(store)


def test_reading_missing_secret_requires_reissue_without_creating_file(tmp_path: Path) -> None:
    from msj.auth import token_secret

    store = _store(tmp_path)
    secret_path = tmp_path / "token.secret"
    with pytest.raises(TokenReissueRequired, match="msj create-token"):
        token_secret(store)
    assert not secret_path.exists()


def test_secret_key_id_mismatch_requires_token_reissue(tmp_path: Path) -> None:
    store = _store(tmp_path)
    token = create_token(store)
    secret_path = tmp_path / "token.secret"
    secret_path.write_bytes(b"y" * 32)
    secret_path.chmod(0o600)
    with pytest.raises(TokenReissueRequired, match="does not match"):
        verify_token(store, token)


def test_read_only_private_secret_mode_is_accepted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    token = create_token(store)
    (tmp_path / "token.secret").chmod(0o400)
    assert verify_token(store, token)


def test_session_validation_does_not_request_a_write_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    token = create_token(store)
    session = new_session(store, store.token_hash() or "")
    ready = threading.Event()

    def hold_writer() -> None:
        connection = sqlite3.connect(store.db_path)
        _ = connection.execute("BEGIN IMMEDIATE")
        ready.set()
        time.sleep(0.5)
        connection.rollback()
        connection.close()

    writer = threading.Thread(target=hold_writer)
    writer.start()
    assert ready.wait(timeout=2)
    started = time.monotonic()
    assert valid_session(store, session)
    elapsed = time.monotonic() - started
    writer.join(timeout=2)
    assert elapsed < 0.25
    assert verify_token(store, token)


def test_login_limiter_counts_failures_only_and_normalizes_ipv6() -> None:
    limiter = LoginLimiter(limit=2, window_s=60)
    client = client_limit_key("2001:db8:1:2::a")
    assert client == client_limit_key("2001:db8:1:2:ffff::1")
    assert limiter.retry_after(client, now=10) is None
    assert limiter.retry_after(client, now=11) is None
    limiter.record_failure(client, now=12)
    assert limiter.retry_after(client, now=13) is None
    limiter.record_failure(client, now=14)
    assert limiter.retry_after(client, now=15) == 57
    assert client_limit_key("192.0.2.1") == "192.0.2.1"


def test_ipv4_mapped_addresses_keep_independent_client_buckets() -> None:
    assert client_limit_key("::ffff:192.0.2.1") == client_limit_key("192.0.2.1")
    assert client_limit_key("::ffff:192.0.2.1") != client_limit_key("::ffff:192.0.2.2")
