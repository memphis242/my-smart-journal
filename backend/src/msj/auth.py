"""Single-user bearer token creation and constant-time verification."""

import hashlib
import hmac
import logging
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Any, Protocol

_logger = logging.getLogger(__name__)


class TokenStore(Protocol):
    settings: Any

    def set_token_hash(self, value: str) -> None: ...
    def token_hash(self) -> str | None: ...


class TokenReissueRequired(RuntimeError):
    """A legacy token digest cannot be verified with the current secret scheme."""


class TokenSecretError(RuntimeError):
    """The private bearer-token key is missing, unsafe, or unreadable."""


def _secret_path(store: TokenStore) -> Path:
    data_dir = store.settings.data_dir
    return Path(data_dir).expanduser() / "token.secret"


def _secret(store: TokenStore) -> bytes:
    path = _secret_path(store)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.lstat()
    except FileNotFoundError:
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(prefix=".token.secret.", dir=path.parent)
            temporary_path = Path(temporary_name)
            value = secrets.token_bytes(32)
            with os.fdopen(descriptor, "wb") as secret_file:
                secret_file.write(value)
                secret_file.flush()
                os.fsync(secret_file.fileno())
            try:
                os.link(temporary_path, path, follow_symlinks=False)
            except FileExistsError:
                return _read_secret(path)
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return value
        except FileExistsError:
            return _read_secret(path)
        except OSError as exc:
            raise TokenSecretError(
                f"Cannot create or read bearer-token key {path} (errno {exc.errno})."
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as exc:
                    _logger.error(
                        "Temporary token-key cleanup failed file=%s errno=%s; "
                        "remove the stale temporary file after checking token.secret",
                        temporary_path,
                        exc.errno,
                    )
    except OSError as exc:
        raise TokenSecretError(
            f"Cannot create or read bearer-token key {path} (errno {exc.errno})."
        ) from exc
    return _read_secret(path)


def _read_secret(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as secret_file:
            metadata = os.fstat(secret_file.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise TokenSecretError(f"Bearer-token key {path} must be a regular file.")
            mode = stat.S_IMODE(metadata.st_mode)
            if mode != 0o600:
                raise TokenSecretError(
                    f"Bearer-token key {path} has mode {mode:04o}; set its permissions to 0600."
                )
            value = secret_file.read(33)
    except OSError as exc:
        raise TokenSecretError(f"Cannot read bearer-token key {path} (errno {exc.errno}).") from exc
    if len(value) != 32:
        raise TokenSecretError(
            f"Bearer-token key {path} must contain exactly 32 bytes; found at least {len(value)}."
        )
    return value


def token_secret(store: TokenStore) -> bytes:
    """Return the private HMAC key used for bearer token digests."""
    return _secret(store)


def create_token(store: TokenStore) -> str:
    """Create a 256-bit access token and persist only its keyed digest."""
    token = secrets.token_urlsafe(32)
    store.set_token_hash(_format_digest(token, _secret(store)))
    return token


def _format_digest(token: str, secret: bytes) -> str:
    digest = hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256${digest}"


def verify_token(store: TokenStore, token: str) -> bool:
    """Verify an access token without exposing stored hash details."""
    return verify_token_hash(token, store.token_hash(), _secret(store))


def verify_token_hash(token: str, stored: str | None, secret: bytes) -> bool:
    """Verify a token against one stored digest snapshot."""
    if stored is None:
        return False
    if stored in {"reissue-required"} or stored.startswith("scrypt$"):
        raise TokenReissueRequired("Bearer token must be re-issued: run `msj create-token`.")
    try:
        algorithm, expected = stored.split("$", maxsplit=1)
        if algorithm != "hmac-sha256" or len(expected) != 64:
            return False
        actual = _format_digest(token, secret).split("$", maxsplit=1)[1]
    except ValueError, TypeError:
        return False
    return hmac.compare_digest(actual, expected)
