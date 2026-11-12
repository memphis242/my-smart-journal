"""Single-user bearer token creation and constant-time verification."""

import hashlib
import hmac
import logging
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Protocol

from .config import Settings
from .diagnostics import SafeMessageError

_logger = logging.getLogger(__name__)
# Digests are produced by _format_digest; anything else in storage is not comparable.
_DIGEST = re.compile(r"hmac-sha256\$(?P<key_id>[0-9a-f]{16})\$(?P<digest>[0-9a-f]{64})")


class TokenStore(Protocol):
    @property
    def settings(self) -> Settings: ...

    def set_token_hash(self, token_hash: str) -> None: ...
    def token_hash(self) -> str | None: ...


class TokenReissueRequired(SafeMessageError):
    """A legacy token digest cannot be verified with the current secret scheme."""

    code = "TOKEN_REISSUE_REQUIRED"


class TokenSecretError(SafeMessageError):
    """The private bearer-token key is missing, unsafe, or unreadable."""

    code = "TOKEN_SECRET_INVALID"


def _secret_path(store: TokenStore) -> Path:
    data_dir = store.settings.data_dir
    return Path(data_dir).expanduser() / "token.secret"


def _secret(store: TokenStore, *, create: bool) -> bytes:
    path = _secret_path(store)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.lstat()
    except FileNotFoundError:
        if not create:
            raise TokenReissueRequired(
                f"Bearer-token key {path} is missing; run `msj create-token` to issue a new token."
            ) from None
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
        except FileExistsError:
            return _read_secret(path)
        except OSError as exc:
            raise TokenSecretError(
                f"Cannot create or read bearer-token key {path} (errno {exc.errno}); "
                "check data-directory ownership and key permissions, then run `msj create-token`."
            ) from exc
        else:
            return value
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
            f"Cannot create or read bearer-token key {path} (errno {exc.errno}); "
            "check data-directory ownership and key permissions, then run `msj create-token`."
        ) from exc
    return _read_secret(path)


def _read_secret(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as secret_file:
            metadata = os.fstat(secret_file.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise TokenSecretError(
                    f"Bearer-token key {path} must be a regular file; restore a private key "
                    "backup or remove the invalid file and run `msj create-token`."
                )
            mode = stat.S_IMODE(metadata.st_mode)
            if mode & 0o077:
                raise TokenSecretError(
                    f"Bearer-token key {path} has mode {mode:04o}; set its permissions to 0600."
                )
            value = secret_file.read(33)
    except OSError as exc:
        raise TokenSecretError(
            f"Cannot read bearer-token key {path} (errno {exc.errno}); "
            "check file ownership and owner-read permissions, then retry."
        ) from exc
    if len(value) != 32:
        raise TokenSecretError(
            f"Bearer-token key {path} must contain exactly 32 bytes; found at least {len(value)}. "
            "Restore a key backup or remove the invalid key and run `msj create-token`."
        )
    return value


def token_secret(store: TokenStore) -> bytes:
    """Return the private HMAC key used for bearer token digests."""
    return _secret(store, create=False)


def create_token(store: TokenStore) -> str:
    """Create a 256-bit access token and persist only its keyed digest."""
    token = secrets.token_urlsafe(32)
    store.set_token_hash(_format_digest(token, _secret(store, create=True)))
    return token


def _format_digest(token: str, secret: bytes) -> str:
    digest = hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()
    key_id = hashlib.sha256(secret).hexdigest()[:16]
    return f"hmac-sha256${key_id}${digest}"


def verify_token(store: TokenStore, token: str) -> bool:
    """Verify an access token without exposing stored hash details."""
    return verify_token_hash(token, store.token_hash(), _secret(store, create=False))


def check_token_secret(store: TokenStore) -> None:
    """Validate that the stored token digest can be served without exposing a token."""
    stored = store.token_hash()
    if stored is None:
        return
    if stored == "reissue-required" or stored.startswith("scrypt$"):
        raise TokenReissueRequired("Bearer token must be re-issued: run `msj create-token`.")
    match = _DIGEST.fullmatch(stored)
    if match is None:
        raise TokenReissueRequired("Bearer token must be re-issued: run `msj create-token`.")
    actual_key_id = hashlib.sha256(_secret(store, create=False)).hexdigest()[:16]
    if not hmac.compare_digest(match["key_id"], actual_key_id):
        raise TokenReissueRequired(
            f"Bearer-token key {_secret_path(store)} does not match the stored token; "
            "run `msj create-token` "
            "to issue a new token."
        )


def verify_token_hash(token: str, stored: str | None, secret: bytes) -> bool:
    """Verify a token against one stored digest snapshot."""
    if stored is None:
        return False
    if stored == "reissue-required" or stored.startswith("scrypt$"):
        raise TokenReissueRequired("Bearer token must be re-issued: run `msj create-token`.")
    if stored.startswith("hmac-sha256$") and stored.count("$") == 1:
        raise TokenReissueRequired(
            "Bearer token digest has no key ID; run `msj create-token` to issue a new token."
        )
    match = _DIGEST.fullmatch(stored)
    if match is None:
        return False
    try:
        current = _DIGEST.fullmatch(_format_digest(token, secret))
    except UnicodeEncodeError:
        # A token with lone surrogates cannot be the issued token.
        return False
    assert current is not None
    if not hmac.compare_digest(match["key_id"], current["key_id"]):
        raise TokenReissueRequired(
            "Bearer-token key does not match the stored token; run `msj create-token` "
            "to issue a new token."
        )
    return hmac.compare_digest(current["digest"], match["digest"])
