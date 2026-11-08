"""Single-user bearer token creation and constant-time verification."""

import base64
import hashlib
import hmac
import secrets
from typing import Protocol


class TokenStore(Protocol):
    def set_token_hash(self, value: str) -> None: ...
    def token_hash(self) -> str | None: ...


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _digest(token: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        token.encode("utf-8"),
        salt=salt,
        n=2**15,
        r=8,
        p=1,
        dklen=32,
        maxmem=128 * 1024 * 1024,
    )


def create_token(store: TokenStore) -> str:
    """Create a high-entropy token and persist only its salted scrypt digest."""
    token = secrets.token_urlsafe(32)
    salt = secrets.token_bytes(16)
    store.set_token_hash(f"scrypt${_encode(salt)}${_encode(_digest(token, salt))}")
    return token


def verify_token(store: TokenStore, token: str) -> bool:
    """Verify an access token without exposing stored hash details."""
    stored = store.token_hash()
    return verify_token_hash(token, stored)


def verify_token_hash(token: str, stored: str | None) -> bool:
    """Verify a token against one stored digest snapshot."""
    if stored is None:
        return False
    try:
        algorithm, encoded_salt, encoded_digest = stored.split("$", maxsplit=2)
        if algorithm != "scrypt":
            return False
        salt = _decode(encoded_salt)
        expected = _decode(encoded_digest)
        actual = _digest(token, salt)
    except ValueError, TypeError:
        return False
    return hmac.compare_digest(actual, expected)
