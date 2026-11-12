"""Properties of entry filtering and date-bound validation."""

import re
from datetime import UTC, date, datetime
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from msj.api import APIProblem, _validate_date_bounds
from msj.entry_views import filter_entries

_FACET_VALUES = st.sampled_from(["a", "A", "b", "B", "Cc", "cC", "ß", "SS", "ss"])
_DATES = st.dates(min_value=date(2020, 1, 1), max_value=date(2020, 1, 20))


@st.composite
def _entries(draw: st.DrawFn) -> list[dict[str, Any]]:
    count = draw(st.integers(0, 12))
    return [
        {
            "id": f"id{index:02d}",
            "entry_date": draw(_DATES).isoformat(),
            "created_at": datetime.fromtimestamp(
                draw(st.integers(1_577_836_800, 1_578_000_000)), UTC
            ).isoformat(),
            "tags": draw(st.lists(_FACET_VALUES, max_size=3)),
            "projects": draw(st.lists(_FACET_VALUES, max_size=3)),
            "skills": draw(st.lists(_FACET_VALUES, max_size=3)),
        }
        for index in range(count)
    ]


def _model(
    entries: list[dict[str, Any]],
    lower: str | None,
    upper: str | None,
    wanted: dict[str, list[str]],
    order: str,
) -> list[str]:
    kept = []
    for entry in entries:
        if lower is not None and entry["entry_date"] < lower:
            continue
        if upper is not None and entry["entry_date"] > upper:
            continue
        if any(
            values and not ({v.casefold() for v in values} & {v.casefold() for v in entry[field]})
            for field, values in wanted.items()
        ):
            continue
        kept.append(entry)
    kept.sort(key=lambda e: (e["entry_date"], e["created_at"], e["id"]), reverse=order == "desc")
    return [e["id"] for e in kept]


@given(
    _entries(),
    st.one_of(st.none(), _DATES.map(date.isoformat)),
    st.one_of(st.none(), _DATES.map(date.isoformat)),
    st.lists(_FACET_VALUES, max_size=2),
    st.lists(_FACET_VALUES, max_size=2),
    st.lists(_FACET_VALUES, max_size=2),
    st.sampled_from(["asc", "desc"]),
)
def test_filter_entries_matches_brute_force_model(
    entries: list[dict[str, Any]],
    lower: str | None,
    upper: str | None,
    tags: list[str],
    projects: list[str],
    skills: list[str],
    order: str,
) -> None:
    result = filter_entries(
        entries,
        from_date=lower,
        to_date=upper,
        tags=tags,
        projects=projects,
        skills=skills,
        order=order,
    )
    wanted = {"tags": tags, "projects": projects, "skills": skills}
    assert [e["id"] for e in result] == _model(entries, lower, upper, wanted, order)


@given(_entries(), st.sampled_from(["asc", "desc"]))
def test_unfiltered_order_is_reverse_of_opposite_order(
    entries: list[dict[str, Any]], order: str
) -> None:
    other = "asc" if order == "desc" else "desc"
    forward = [e["id"] for e in filter_entries(entries, order=order)]
    backward = [e["id"] for e in filter_entries(entries, order=other)]
    assert forward == backward[::-1]


_CANONICAL = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _is_canonical_date(value: str) -> bool:
    if _CANONICAL.fullmatch(value) is None:
        return False
    try:
        _ = date(int(value[:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        return False
    return True


_date_like = st.one_of(
    st.text(max_size=14),
    st.text(st.sampled_from("0123456789-W"), max_size=12),
    st.dates().map(date.isoformat),
    st.dates().map(lambda d: d.strftime("%Y%m%d")),
    st.dates().map(lambda d: d.isoformat() + " "),
    st.dates().map(lambda d: d.strftime("%G-W%V-%u")),
    st.dates().map(lambda d: d.isoformat().replace("-0", "-")),
)


@given(_date_like)
def test_date_bounds_accept_only_canonical_dates(value: str) -> None:
    if _is_canonical_date(value):
        _validate_date_bounds(value, None)
        return
    with pytest.raises(APIProblem) as raised:
        _validate_date_bounds(value, None)
    assert raised.value.status == 422
    assert raised.value.code == "INVALID_DATE_FILTER"


@given(_date_like)
def test_filter_entries_rejects_what_the_api_validator_rejects(value: str) -> None:
    api_accepts = True
    try:
        _validate_date_bounds(None, value)
    except APIProblem:
        api_accepts = False
    try:
        _ = filter_entries([], to_date=value)
    except ValueError:
        assert not api_accepts
    else:
        assert api_accepts
