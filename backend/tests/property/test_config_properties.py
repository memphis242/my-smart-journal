"""Properties of configuration validation and loading."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from msj.config import EmbeddingSettings, Settings, TaskRoute, load_settings

_HOST = st.from_regex(r"[a-z][a-z0-9]{0,8}(\.[a-z][a-z0-9]{0,8}){0,2}", fullmatch=True)
_PATH = st.from_regex(r"(/[a-z0-9]{1,6}){0,3}", fullmatch=True)


def _route_accepted(base_url: str) -> bool:
    try:
        _ = TaskRoute(base_url=base_url)
    except ValidationError:
        return False
    return True


@given(st.text(max_size=60))
def test_accepted_base_urls_satisfy_the_url_invariant(base_url: str) -> None:
    if not _route_accepted(base_url):
        return
    address = urlsplit(base_url)
    assert address.scheme in {"http", "https"}
    assert address.hostname
    assert address.username is None and address.password is None
    assert not address.query and not address.fragment


@given(
    st.sampled_from(["http", "https"]), _HOST, st.one_of(st.none(), st.integers(1, 65535)), _PATH
)
def test_plain_http_urls_are_accepted(scheme: str, host: str, port: int | None, path: str) -> None:
    suffix = f":{port}" if port is not None else ""
    assert _route_accepted(f"{scheme}://{host}{suffix}{path}")


@given(
    st.sampled_from(["http", "https"]),
    _HOST,
    _PATH,
    st.sampled_from(["?k=v", "#frag", "?a", "#a"]),
)
def test_query_and_fragment_urls_are_rejected(scheme: str, host: str, path: str, tail: str) -> None:
    assert not _route_accepted(f"{scheme}://{host}{path}{tail}")


@given(st.sampled_from(["http", "https"]), _HOST, st.text("abc", min_size=1, max_size=4))
def test_userinfo_urls_are_rejected(scheme: str, host: str, user: str) -> None:
    assert not _route_accepted(f"{scheme}://{user}@{host}/v1")
    assert not _route_accepted(f"{scheme}://{user}:{user}@{host}/v1")


@given(st.sampled_from(["ftp", "file", "ws", "", "javascript"]), _HOST)
def test_non_http_schemes_are_rejected(scheme: str, host: str) -> None:
    assert not _route_accepted(f"{scheme}://{host}/v1")


@given(st.integers(1, 4096), st.integers(0, 2048))
def test_chunk_overlap_must_be_smaller_than_chunk_size(tokens: int, overlap: int) -> None:
    try:
        built = EmbeddingSettings(provider="fake", chunk_tokens=tokens, chunk_overlap=overlap)
    except ValidationError:
        assert overlap >= tokens
    else:
        assert overlap < tokens
        assert (built.chunk_tokens, built.chunk_overlap) == (tokens, overlap)


_seconds = st.floats(min_value=0.001, max_value=100_000, allow_nan=False, allow_infinity=False)


@settings(max_examples=60)
@given(_seconds, _seconds)
def test_lease_must_exceed_provider_timeout_by_more_than_thirty_seconds(
    lease: float, timeout: float
) -> None:
    try:
        built = Settings(
            data_dir=Path("/nonexistent-msj"), lease_s=lease, provider_timeout_s=timeout
        )
    except ValidationError:
        assert lease <= timeout + 30
    else:
        assert lease > timeout + 30
        assert built.lease_s == lease


@settings(max_examples=30)
@given(
    st.integers(1, 500),
    st.integers(1, 500),
    st.one_of(st.none(), st.integers(1, 500)),
    st.one_of(st.none(), st.integers(1, 500)),
    st.one_of(st.none(), st.integers(1, 500)),
)
def test_load_settings_merges_toml_with_environment_winning(
    tmp_path_factory: pytest.TempPathFactory,
    toml_login: int,
    toml_bearer: int,
    toml_top_k: int | None,
    env_login: int | None,
    env_top_k: int | None,
) -> None:
    directory = tmp_path_factory.mktemp("config")
    lines = [f"login_rate_limit = {toml_login}", f"bearer_rate_limit = {toml_bearer}"]
    if toml_top_k is not None:
        lines += ["[embedding]", f"top_k = {toml_top_k}"]
    config = directory / "msj.toml"
    config.write_text("\n".join(lines) + "\n")
    with pytest.MonkeyPatch.context() as env:
        env.setenv("MSJ_CONFIG_FILE", str(config))
        env.setenv("MSJ_DATA_DIR", str(directory / "data"))
        if env_login is not None:
            env.setenv("MSJ_LOGIN_RATE_LIMIT", str(env_login))
        if env_top_k is not None:
            env.setenv("MSJ_EMBEDDING__TOP_K", str(env_top_k))
        loaded = load_settings()
    assert loaded.login_rate_limit == (env_login if env_login is not None else toml_login)
    assert loaded.bearer_rate_limit == toml_bearer
    expected_top_k = env_top_k if env_top_k is not None else toml_top_k
    assert loaded.embedding.top_k == (expected_top_k if expected_top_k is not None else 20)
