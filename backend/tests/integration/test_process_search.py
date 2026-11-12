"""NDJSON tiered search and RRF retrieval over processed entries on a real server.

Each entry is written so that exactly one tier is the first to match its query:

- exact: a case-sensitive phrase;
- substring: the query differs only in case;
- fuzzy: a one-letter typo (RapidFuzz partial ratio 90 against the default threshold 80);
- deterministic (FTS5): one shared token, but no substring and fuzzy scores below 67;
- semantic: the fake embedder splits ``tundraλmosaic`` on the non-ASCII letter into the
  query's tokens, while FTS5's unicode61 tokenizer keeps it one token.
"""

import json
from typing import Any

import pytest

from .msj_harness import MsjInstance

pytestmark = pytest.mark.integration

ENTRIES = {
    "exact": "Calibrated the Heliotrope array before lunch.",
    "substring": "Watered the marigolds along the fence.",
    "fuzzy": "Tuned the quasarbeem emitter at the lab.",
    "fts": "A cold zephyr rattled the shutters all night.",
    "semantic": "Notes: tundraλmosaic quiet.",
}
# key -> (query, tier that first matches the entry)
QUERIES = {
    "exact": ("Heliotrope", 1),
    "substring": ("MARIGOLDS", 2),
    "fuzzy": ("quasarbeam", 3),
    "fts": ("zephyr lanterns", 4),
    "semantic": ("mosaic tundra", 5),
}
TIER_NAMES = {1: "exact", 2: "substring", 3: "fuzzy", 4: "deterministic", 5: "semantic"}


def _search(msj: MsjInstance, query: str, tiers: str | None) -> list[dict[str, Any]]:
    params = {"q": query} | ({"tiers": tiers} if tiers is not None else {})
    response = msj.api.get("/api/v1/search", params=params)
    body = msj.expect(response, 200)
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert isinstance(body, str)
    assert body.endswith("\n"), "NDJSON stream must end with a newline"
    events = [json.loads(line) for line in body.splitlines()]
    assert all(isinstance(event, dict) for event in events)
    return events


def _check_framing(events: list[dict[str, Any]], tiers: tuple[int, ...]) -> None:
    *tier_events, done = events
    assert done.get("done") is True, done
    assert "error" not in done, done
    assert [event["tier"] for event in tier_events] == list(tiers)
    seen: set[str] = set()
    for event in tier_events:
        assert event["name"] == TIER_NAMES[event["tier"]], event
        assert isinstance(event["elapsed_ms"], int | float)
        for result in event["results"]:
            assert {"entry_id", "score", "snippet", "entry"} <= result.keys(), result
            assert result["entry"]["id"] == result["entry_id"]
            assert result["entry"]["status"] == "done"
            assert result["entry"]["version"] == 1
            assert result["entry_id"] not in seen, "an entry appeared in two tiers"
            seen.add(result["entry_id"])
            if event["tier"] == 5:
                assert result["passage"], result
        if event["tier"] == 5:
            # Every entry is embedded, so the semantic tier reports no unavailability.
            assert "unavailable" not in event, event


def _first_tier(events: list[dict[str, Any]], entry_id: str) -> int | None:
    for event in events:
        if any(result["entry_id"] == entry_id for result in event.get("results", [])):
            return int(event["tier"])
    return None


def test_each_search_tier_and_retrieval(msj: MsjInstance) -> None:
    ids = {key: msj.create_text_entry(text)["id"] for key, text in ENTRIES.items()}
    msj.process_entries(list(ids.values()))

    mismatches: list[str] = []
    for key, (query, tier) in QUERIES.items():
        for tiers in (str(tier), None):
            selected = (tier,) if tiers is not None else (1, 2, 3, 4, 5)
            events = _search(msj, query, tiers)
            _check_framing(events, selected)
            found = _first_tier(events, ids[key])
            if found != tier:
                mismatches.append(
                    f"q={query!r} tiers={tiers or 'default'}: {key} entry in tier {found}, "
                    f"expected {tier}; events={events}"
                )
    assert not mismatches, "\n".join(mismatches)

    # A tier subset that excludes the first matching tier does not leak the entry.
    events = _search(msj, "Heliotrope", "2,3,4")
    _check_framing(events, (2, 3, 4))
    assert _first_tier(events, ids["exact"]) == 2

    for query, key in (("zephyr", "fts"), ("mosaic tundra", "semantic")):
        retrieved = msj.expect(msj.api.post("/api/v1/retrieve", json={"query": query, "k": 3}), 200)
        assert retrieved["unavailable"] is None, retrieved
        assert 1 <= len(retrieved["items"]) <= 3
        top = retrieved["items"][0]
        assert top["entry"]["id"] == ids[key], retrieved
        assert top["score"] > 0
        assert top["passages"], top

    _ = msj.expect_problem(
        msj.api.post("/api/v1/retrieve", json={"query": "zephyr", "k": True}),
        422,
        "INVALID_RETRIEVAL_REQUEST",
    )
    _ = msj.expect_problem(
        msj.api.get("/api/v1/search", params={"q": "zephyr", "tiers": "3,3"}),
        422,
        "INVALID_SEARCH_FILTER",
    )
