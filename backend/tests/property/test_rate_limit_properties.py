"""Properties of rate-limit keys and the login limiter."""

import ipaddress

from hypothesis import assume, given
from hypothesis import strategies as st

from msj.web_auth import LoginLimiter, client_limit_key

_V6 = st.ip_addresses(v=6)
_V4 = st.ip_addresses(v=4)
_SUFFIX = st.integers(0, 2**64 - 1)


def _mapped(address: ipaddress.IPv6Address) -> bool:
    return address.ipv4_mapped is not None


@given(st.integers(0, 2**64 - 1), _SUFFIX, _SUFFIX)
def test_ipv6_addresses_in_one_slash_64_share_a_key(prefix: int, first: int, second: int) -> None:
    one = ipaddress.IPv6Address((prefix << 64) | first)
    two = ipaddress.IPv6Address((prefix << 64) | second)
    assume(not _mapped(one) and not _mapped(two))
    assert client_limit_key(str(one)) == client_limit_key(str(two))
    assert client_limit_key(one.exploded) == client_limit_key(str(one))


@given(_V6, _V6)
def test_ipv6_addresses_in_different_slash_64_do_not_share_a_key(
    one: ipaddress.IPv6Address, two: ipaddress.IPv6Address
) -> None:
    assume(int(one) >> 64 != int(two) >> 64)
    assume(not _mapped(one) and not _mapped(two))
    assert client_limit_key(str(one)) != client_limit_key(str(two))


@given(_V4)
def test_ipv4_mapped_ipv6_maps_to_the_ipv4_key(address: ipaddress.IPv4Address) -> None:
    mapped = ipaddress.IPv6Address(f"::ffff:{address}")
    assert client_limit_key(str(mapped)) == client_limit_key(str(address)) == str(address)


@given(st.one_of(st.none(), st.text(max_size=60)))
def test_arbitrary_client_strings_never_raise(address: str | None) -> None:
    key = client_limit_key(address)
    assert isinstance(key, str)


@given(_V6, st.text("abcdefgh0123", min_size=1, max_size=6))
def test_ipv6_with_zone_id_does_not_raise(address: ipaddress.IPv6Address, zone: str) -> None:
    assert client_limit_key(f"{address}%{zone}")


@given(
    st.integers(1, 6),
    st.floats(1, 100, allow_nan=False),
    st.lists(
        st.tuples(st.sampled_from(["a", "b", "c"]), st.floats(0, 40, allow_nan=False)),
        max_size=60,
    ),
)
def test_limiter_never_admits_more_than_limit_failures_per_window(
    limit: int, window: float, steps: list[tuple[str, float]]
) -> None:
    limiter = LoginLimiter(limit, window)
    clock = 0.0
    admitted: dict[str, list[float]] = {}
    for key, advance in steps:
        clock += advance
        retry = limiter.retry_after(key, clock)
        if retry is None:
            limiter.record_failure(key, clock)
            admitted.setdefault(key, []).append(clock)
        else:
            assert retry >= 1
    for times in admitted.values():
        for moment in times:
            inside = [t for t in times if moment - window < t <= moment]
            assert len(inside) <= limit


@given(st.integers(1, 6), st.floats(1, 100, allow_nan=False))
def test_limiter_blocks_exactly_at_limit_and_recovers_after_window(
    limit: int, window: float
) -> None:
    limiter = LoginLimiter(limit, window)
    for _ in range(limit):
        assert limiter.retry_after("k", 0.0) is None
        limiter.record_failure("k", 0.0)
    assert limiter.retry_after("k", 0.0) is not None
    assert limiter.retry_after("other", 0.0) is None
    assert limiter.retry_after("k", window) is None
