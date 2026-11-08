"""Opaque SQLite-backed browser sessions and bounded login rate limiting."""

import hashlib
import ipaddress
import secrets
import threading
import time
from collections import OrderedDict, deque

from .storage import Store, timestamp

SESSION_TTL_S = 30 * 24 * 60 * 60


class LoginLimiter:
    def __init__(self, limit: int = 10, window_s: float = 60, max_clients: int = 10_000) -> None:
        self._limit = limit
        self._window_s = window_s
        self._max_clients = max_clients
        self._attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def retry_after(self, key: str, now: float | None = None) -> int | None:
        clock = time.monotonic() if now is None else now
        with self._lock:
            attempts = self._attempts.get(key, deque())
            while attempts and clock - attempts[0] >= self._window_s:
                attempts.popleft()
            if len(attempts) >= self._limit:
                return max(1, int(self._window_s - (clock - attempts[0])))
            return None

    def record_failure(self, key: str, now: float | None = None) -> None:
        clock = time.monotonic() if now is None else now
        with self._lock:
            attempts = self._attempts.setdefault(key, deque())
            self._attempts.move_to_end(key)
            while attempts and clock - attempts[0] >= self._window_s:
                attempts.popleft()
            attempts.append(clock)
            while len(self._attempts) > self._max_clients:
                self._attempts.popitem(last=False)


def client_limit_key(address: str | None) -> str:
    """Normalize client addresses and group IPv6 clients by their /64."""
    if address is None:
        return "unknown"
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return str(parsed)


class TokenChanged(RuntimeError):
    """The configured token changed while a browser session was being created."""


def new_session(store: Store, expected_token_hash: str) -> str:
    value = secrets.token_urlsafe(32)
    digest = hashlib.sha256(value.encode("ascii")).hexdigest()
    now = time.time()
    with store.connect() as db:
        _ = db.execute("BEGIN IMMEDIATE")
        auth_row = db.execute("SELECT token_hash FROM auth WHERE id=1").fetchone()
        if auth_row is None or auth_row["token_hash"] != expected_token_hash:
            raise TokenChanged
        _ = db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
        _ = db.execute(
            "INSERT INTO sessions(session_hash,created_at,expires_at) VALUES(?,?,?)",
            (digest, timestamp(), now + SESSION_TTL_S),
        )
    return value


def valid_session(store: Store, value: str | None) -> bool:
    if not value:
        return False
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    now = time.time()
    with store.connect() as db:
        row = db.execute(
            "SELECT 1 FROM sessions WHERE session_hash=? AND expires_at>?", (digest, now)
        ).fetchone()
    return row is not None


def revoke_session(store: Store, value: str | None) -> None:
    if value:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        with store.connect() as db:
            _ = db.execute("DELETE FROM sessions WHERE session_hash=?", (digest,))
