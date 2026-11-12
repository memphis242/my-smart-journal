"""Properties of HTTP framing and JSON input robustness."""

import json
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

# Lone surrogates are exercised by dedicated tests below, so the free-form generator stays
# deterministic about which inputs are known to be rejected cleanly.
_STRINGS = st.text(max_size=30)
_LINE_SEPARATOR = chr(0x2028)
_JSON = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        _STRINGS,
    ),
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(_STRINGS, inner, max_size=4)
    ),
    max_leaves=8,
)
_LONE_SURROGATE_TEXT = st.builds(
    lambda head, mark, tail: head + mark + tail,
    st.text(max_size=5),
    st.sampled_from(["\ud800", "\udfff", "\udc00"]),
    st.text(max_size=5),
)


def _dump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")


def _assert_acceptable(response: Any) -> None:
    assert response.status_code < 500, response.text[:300]
    if response.status_code >= 400:
        body = response.json()
        assert isinstance(body["code"], str) and body["code"]


@settings(max_examples=25)
@given(
    st.lists(
        st.text(st.characters(exclude_characters="\x00"), min_size=1, max_size=40).filter(
            lambda s: bool(s.strip()) and "\n" not in s
        ),
        min_size=1,
        max_size=4,
    ),
    st.sampled_from(["seed", "journal", "needle", "x", _LINE_SEPARATOR, "'"]),
)
def test_search_stream_is_well_formed_ndjson(api: Any, texts: list[str], query: str) -> None:
    for text in texts:
        created = api.client.post("/api/v1/entries", headers=api.headers, json={"text": text})
        assert created.status_code == 201
    queries = [query, texts[0].strip()[:20]]
    for candidate in queries:
        response = api.client.get("/api/v1/search", params={"q": candidate}, headers=api.headers)
        if response.status_code == 422:
            assert response.json()["code"] == "INVALID_QUERY"
            continue
        assert response.status_code == 200
        lines = response.content.split(b"\n")
        assert lines[-1] == b""
        events = [json.loads(line) for line in lines[:-1]]
        assert all(isinstance(event, dict) for event in events)
        done = [i for i, event in enumerate(events) if event.get("done") is True]
        assert done == [len(events) - 1]
        assert "error" not in events[-1]
        for event in events[:-1]:
            for result in event["results"]:
                assert isinstance(result["entry"], dict)
                assert result["entry"]["id"] == result["entry_id"]


@settings(max_examples=25)
@given(_JSON)
def test_post_entries_survives_arbitrary_json(api: Any, payload: Any) -> None:
    _assert_acceptable(
        api.client.post(
            "/api/v1/entries",
            headers=api.headers | {"Content-Type": "application/json"},
            content=_dump(payload),
        )
    )


@settings(max_examples=25)
@given(
    st.dictionaries(
        st.sampled_from(["text", "title", "tags", "entry_date", "expected_version"]), _JSON
    )
)
def test_patch_entry_survives_arbitrary_json(api: Any, payload: dict[str, Any]) -> None:
    _assert_acceptable(
        api.client.patch(
            f"/api/v1/entries/{api.entry_id}",
            headers=api.headers | {"Content-Type": "application/json"},
            content=_dump(payload),
        )
    )


@settings(max_examples=25)
@given(_JSON, st.sampled_from(["retrieve", "export", "compilations", "session"]))
def test_json_endpoints_survive_arbitrary_json(api: Any, payload: Any, endpoint: str) -> None:
    headers = {} if endpoint == "session" else api.headers
    _assert_acceptable(
        api.client.post(
            f"/api/v1/{endpoint}",
            headers=headers | {"Content-Type": "application/json"},
            content=_dump(payload),
        )
    )


@settings(max_examples=25)
@given(
    st.sampled_from(["retrieve", "export", "compilations", "session", "entries"]),
    st.binary(max_size=200),
)
def test_json_endpoints_survive_arbitrary_bytes(api: Any, endpoint: str, body: bytes) -> None:
    headers = {} if endpoint == "session" else api.headers
    _assert_acceptable(
        api.client.post(
            f"/api/v1/{endpoint}",
            headers=headers | {"Content-Type": "application/json"},
            content=body,
        )
    )


def _surrogate_requests(api: Any, value: str) -> dict[str, tuple[str, str, Any]]:
    return {
        "text": ("post", "/api/v1/entries", {"text": value}),
        "date": ("post", "/api/v1/entries", {"text": "body", "entry_date": value}),
        "session": ("post", "/api/v1/session", {"token": value}),
        "title": ("post", "/api/v1/entries", {"text": "body", "title": value}),
        "tag": ("post", "/api/v1/entries", {"text": "body", "tags": [value]}),
        "query": ("post", "/api/v1/retrieve", {"query": value}),
        "purpose": (
            "post",
            "/api/v1/compilations",
            {"entry_ids": [api.entry_id], "purpose": value, "period": value},
        ),
        "export_ids": ("post", "/api/v1/export", {"entry_ids": [value], "format": "json"}),
        "patch_text": ("patch", f"/api/v1/entries/{api.entry_id}", {"text": value}),
        "patch_title": ("patch", f"/api/v1/entries/{api.entry_id}", {"title": value}),
    }


def _send_surrogate(api: Any, field: str, value: str) -> None:
    method, path, payload = _surrogate_requests(api, value)[field]
    headers = {} if field == "session" else api.headers
    _assert_acceptable(
        api.client.request(
            method.upper(),
            path,
            headers=headers | {"Content-Type": "application/json"},
            content=_dump(payload),
        )
    )


@settings(max_examples=25)
@given(_LONE_SURROGATE_TEXT, st.sampled_from(["text", "date", "session"]))
def test_lone_surrogates_in_validated_fields_are_client_errors(
    api: Any, value: str, field: str
) -> None:
    _send_surrogate(api, field, value)


# Regression: lone-surrogate escapes reached SQLite binding and the JSON response encoder
# (title, tags, retrieve query, compilation purpose/period, PATCH text/title, echoed export
# ids) and produced 500 instead of a 4xx problem.
@settings(max_examples=25)
@given(
    _LONE_SURROGATE_TEXT,
    st.sampled_from(
        ["title", "tag", "query", "purpose", "export_ids", "patch_text", "patch_title"]
    ),
)
def test_lone_surrogates_in_stored_or_echoed_fields_are_client_errors(
    api: Any, value: str, field: str
) -> None:
    _send_surrogate(api, field, value)


def test_lone_surrogate_in_multipart_tags_is_a_client_error(api: Any) -> None:
    # Regression: the multipart `tags` field is JSON text and took the same path to SQLite.
    response = api.client.post(
        "/api/v1/entries",
        headers=api.headers,
        data={"tags": '["\\ud800"]'},
        files={"file": ("clip.wav", b"RIFF0000WAVEfmt ", "audio/wav")},
    )
    assert response.status_code == 422
    _assert_acceptable(response)
