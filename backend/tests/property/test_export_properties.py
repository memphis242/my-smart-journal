"""Properties of journal export rendering and the export endpoint."""

import json
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from msj.exporting import render

_SAFE = st.text(st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=12)
_SAFE_PHRASE = st.lists(_SAFE, min_size=1, max_size=4).map(" ".join)
_TEXT_SEPARATOR = "\n\n--------------------\n\n"
_MD_SEPARATOR = "\n\n---\n\n"
_INCLUDE_KEYS = ("date", "title", "tags", "summary", "text", "extraction")
_DEFAULT_ON = {"date", "title", "tags", "summary", "text"}
_JSON_KEYS = {
    "date": "entry_date",
    "title": "title",
    "tags": "tags",
    "summary": "summary",
    "text": "text",
    "extraction": "extraction",
}

_json_leaf = st.one_of(st.none(), st.booleans(), st.integers(), st.text())
_json_value = st.recursive(
    _json_leaf,
    lambda inner: st.one_of(
        st.lists(inner, max_size=3), st.dictionaries(st.text(), inner, max_size=3)
    ),
    max_leaves=6,
)


@st.composite
def _entries(draw: st.DrawFn, *, safe: bool) -> list[dict[str, Any]]:
    word = _SAFE_PHRASE if safe else st.text(max_size=30)
    extraction_lists = st.lists(word, min_size=1, max_size=3)
    extraction = (
        st.fixed_dictionaries(
            {},
            optional={
                "projects": extraction_lists,
                "skills_technologies": extraction_lists,
                "accomplishments": extraction_lists,
                "tags": extraction_lists,
            },
        )
        if safe
        else _json_value.filter(lambda v: isinstance(v, dict))
    )
    count = draw(st.integers(0, 6))
    return [
        {
            "id": f"id{index}",
            "entry_date": f"2024-0{draw(st.integers(1, 3))}-1{draw(st.integers(0, 3))}",
            "created_at": f"2024-01-0{draw(st.integers(1, 3))}T00:00:0{draw(st.integers(0, 3))}Z",
            "title": draw(st.one_of(st.none(), word)),
            "tags": draw(st.one_of(st.none(), st.lists(word, max_size=3))),
            "summary": draw(st.one_of(st.none(), word)),
            "text": draw(st.one_of(st.none(), word)),
            "extraction": draw(st.one_of(st.none(), extraction)),
        }
        for index in range(count)
    ]


_includes = st.dictionaries(st.sampled_from(_INCLUDE_KEYS), st.booleans())


def _ordered(entries: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    return sorted(
        entries,
        key=lambda e: (e["entry_date"], e["created_at"], e["id"]),
        reverse=order == "desc",
    )


@given(_entries(safe=False), _includes, st.sampled_from(["asc", "desc"]))
def test_json_export_round_trips_included_fields_in_order(
    entries: list[dict[str, Any]], include: dict[str, bool], order: str
) -> None:
    content, media_type, extension = render(
        entries, output_format="json", include=include, order=order
    )
    assert (media_type, extension) == ("application/json", "json")
    expected = []
    for entry in _ordered(entries, order):
        row: dict[str, Any] = {"id": entry["id"]}
        for flag, key in _JSON_KEYS.items():
            if include.get(flag, flag in _DEFAULT_ON) and entry.get(key) is not None:
                row[key] = entry[key]
        expected.append(row)
    assert json.loads(content) == expected


def _model_header(entry: dict[str, Any], include: dict[str, bool]) -> str:
    date = entry["entry_date"] if include.get("date", True) else ""
    title = (entry.get("title") or "") if include.get("title", True) else ""
    return "  ".join(part for part in (date, title) if part)


@given(_entries(safe=True), _includes, st.sampled_from(["asc", "desc"]))
def test_text_export_has_headers_separators_and_no_markdown_markers(
    entries: list[dict[str, Any]], include: dict[str, bool], order: str
) -> None:
    content, media_type, extension = render(
        entries, output_format="text", include=include, order=order
    )
    assert (media_type, extension) == ("text/plain; charset=utf-8", "txt")
    output = content.decode()
    assert output.count(_TEXT_SEPARATOR) == max(0, len(entries) - 1)
    if not entries:
        assert output == ""
        return
    blocks = output.split(_TEXT_SEPARATOR)
    assert len(blocks) == len(entries)
    for block, entry in zip(blocks, _ordered(entries, order), strict=True):
        header = _model_header(entry, include)
        if header:
            assert block.split("\n", 1)[0] == header
        for line in block.splitlines():
            stripped = line.lstrip()
            assert not stripped.startswith(("#", "- ", "* ", "**"))


@given(_entries(safe=True), _includes, st.sampled_from(["asc", "desc"]))
def test_markdown_export_contains_included_texts_and_consistent_separators(
    entries: list[dict[str, Any]], include: dict[str, bool], order: str
) -> None:
    content, media_type, extension = render(
        entries, output_format="markdown", include=include, order=order
    )
    assert (media_type, extension) == ("text/markdown", "md")
    output = content.decode()
    assert output.count(_MD_SEPARATOR) == max(0, len(entries) - 1)
    blocks = output.split(_MD_SEPARATOR) if entries else []
    assert len(blocks) == len(entries)
    for block, entry in zip(blocks, _ordered(entries, order), strict=True):
        for flag in ("text", "summary"):
            if include.get(flag, True) and entry[flag]:
                assert entry[flag] in block
        if include.get("title", True) and entry["title"]:
            assert entry["title"] in block
        if include.get("date", True):
            assert entry["entry_date"] in block


@given(_entries(safe=False), st.sampled_from(["text", "markdown", "json"]), _includes)
def test_every_format_renders_arbitrary_entries_to_bytes(
    entries: list[dict[str, Any]], fmt: str, include: dict[str, bool]
) -> None:
    content, _, _ = render(entries, output_format=fmt, include=include, order="desc")
    assert isinstance(content, bytes)


@settings(max_examples=25)
@given(
    st.lists(_SAFE_PHRASE, min_size=1, max_size=3),
    st.sampled_from(["text", "markdown", "json"]),
    st.sampled_from(["asc", "desc"]),
)
def test_export_endpoint_serves_attachment_with_no_store(
    api: Any, texts: list[str], fmt: str, order: str
) -> None:
    ids = []
    for text in texts:
        created = api.client.post("/api/v1/entries", headers=api.headers, json={"text": text})
        assert created.status_code == 201
        ids.append(created.json()["id"])
    response = api.client.post(
        "/api/v1/export",
        headers=api.headers,
        json={"entry_ids": ids, "format": fmt, "order": order},
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    assert "no-store" in response.headers["cache-control"]
    assert response.content
    if fmt == "json":
        assert {row["id"] for row in response.json()} == set(ids)
