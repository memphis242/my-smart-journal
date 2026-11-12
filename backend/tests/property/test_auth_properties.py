"""Properties of bearer-token digests and verification."""

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from msj.auth import TokenReissueRequired, _format_digest, verify_token_hash

_SECRET = st.binary(min_size=32, max_size=32)
_TOKEN = st.text(min_size=1, max_size=80)
_ANY_TEXT = st.text(st.characters(exclude_categories=[]), max_size=100)


@given(_TOKEN, _SECRET)
def test_issued_token_verifies(token: str, secret: bytes) -> None:
    assert verify_token_hash(token, _format_digest(token, secret), secret) is True


@given(_TOKEN, _TOKEN, _SECRET)
def test_any_other_token_is_rejected(token: str, other: str, secret: bytes) -> None:
    assume(token != other)
    assert verify_token_hash(other, _format_digest(token, secret), secret) is False


@given(_TOKEN, _SECRET, _SECRET)
def test_wrong_key_requires_reissue(token: str, secret: bytes, other_secret: bytes) -> None:
    assume(secret != other_secret)
    with pytest.raises(TokenReissueRequired):
        _ = verify_token_hash(token, _format_digest(token, secret), other_secret)


@given(_TOKEN, _SECRET)
def test_missing_digest_rejects_without_error(token: str, secret: bytes) -> None:
    assert verify_token_hash(token, None, secret) is False


@given(_ANY_TEXT, _SECRET)
def test_unverifiable_tokens_never_raise(token: str, secret: bytes) -> None:
    stored = _format_digest("fixed-token", secret)
    assert verify_token_hash(token, stored, secret) is (token == "fixed-token")


_ASCII_DIGEST = st.text(st.characters(codec="ascii"), max_size=120)
_HEXISH = st.text(st.sampled_from("0123456789abcdef"), min_size=16, max_size=16)
_STRUCTURED = st.builds(
    "hmac-sha256${}${}".format,
    st.text(st.characters(codec="ascii"), max_size=20),
    st.text(st.characters(codec="ascii"), max_size=70),
)


@given(_TOKEN, _SECRET, st.one_of(_ASCII_DIGEST, _STRUCTURED))
def test_malformed_ascii_digests_return_false_or_request_reissue(
    token: str, secret: bytes, stored: str
) -> None:
    try:
        result = verify_token_hash(token, stored, secret)
    except TokenReissueRequired:
        return
    assert isinstance(result, bool)


# Regression: non-ASCII stored key IDs reached hmac.compare_digest, which raised TypeError.
@given(
    _TOKEN,
    _SECRET,
    st.builds(
        "hmac-sha256${}${}".format,
        st.text(
            st.characters(min_codepoint=128, exclude_categories=["Cs"]), min_size=16, max_size=16
        ),
        st.text(st.sampled_from("0123456789abcdef"), min_size=64, max_size=64),
    ),
)
def test_malformed_non_ascii_digests_return_false_or_request_reissue(
    token: str, secret: bytes, stored: str
) -> None:
    try:
        result = verify_token_hash(token, stored, secret)
    except TokenReissueRequired:
        return
    assert isinstance(result, bool)
