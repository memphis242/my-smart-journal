"""Properties of query validation, tiered search, and rank fusion."""

import math
from collections.abc import Mapping, Sequence
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from msj.api import _valid_query
from msj.search import _corpus, reciprocal_rank_fusion, tiered_search

# Characters whose casefold keeps the character count, so snippet offsets stay aligned.
_STABLE_CHARS = st.characters(exclude_categories=["Cs"], exclude_characters="\x00").filter(
    lambda c: len(c.casefold()) == 1
)
_WORDISH = st.text(_STABLE_CHARS, max_size=40)
_ALPHABET_SMALL = st.sampled_from("abAB xyXY1")


def _is_control(character: str) -> bool:
    return ord(character) < 32 or 127 <= ord(character) <= 159


@given(st.text(max_size=600))
def test_valid_query_matches_independent_model(query: str) -> None:
    expected = bool(query.strip()) and len(query) <= 500 and not any(_is_control(c) for c in query)
    assert _valid_query(query) is expected


@given(
    st.text(st.characters(exclude_categories=["Cs", "Cc"]), min_size=1, max_size=500).filter(
        lambda s: bool(s.strip())
    )
)
def test_printable_nonblank_queries_up_to_limit_are_valid(query: str) -> None:
    assert _valid_query(query)


@given(st.text(max_size=20), st.sampled_from(["\x00", "\n", "\t", "\x1f", "\x7f", "\x85", "\x9f"]))
def test_any_control_character_invalidates_query(prefix: str, control: str) -> None:
    assert not _valid_query(prefix + "needle" + control)


@given(st.integers(min_value=501, max_value=700))
def test_over_long_queries_are_invalid(length: int) -> None:
    assert not _valid_query("a" * length)


_documents = st.lists(
    st.fixed_dictionaries(
        {
            "text": st.text(_ALPHABET_SMALL, max_size=30),
            "summary": st.one_of(st.none(), st.text(_ALPHABET_SMALL, max_size=15)),
            "title": st.text(_ALPHABET_SMALL, max_size=10),
            "tags": st.text(_ALPHABET_SMALL, max_size=8),
        }
    ),
    max_size=8,
).map(lambda rows: [row | {"entry_id": f"e{index}"} for index, row in enumerate(rows)])


@st.composite
def _search_case(
    draw: st.DrawFn,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], tuple[int, ...]]:
    documents = draw(_documents)
    query = draw(st.text(_ALPHABET_SMALL, max_size=6))
    ranked_ids = draw(st.lists(st.sampled_from([f"e{i}" for i in range(10)]), max_size=10))
    fts = [
        {"entry_id": entry_id, "rank": draw(st.floats(-50, 5, allow_nan=False))}
        for entry_id in ranked_ids
    ]
    tiers = tuple(draw(st.lists(st.integers(1, 5), unique=True, max_size=5)))
    return query, documents, fts, tiers


@given(_search_case())
def test_tiered_search_event_structure(
    case: tuple[str, list[dict[str, Any]], list[dict[str, Any]], tuple[int, ...]],
) -> None:
    query, documents, fts, tiers = case
    events = list(tiered_search(query, documents, fts, tiers=tiers))

    *tier_events, final = events
    assert final.get("done") is True
    assert all("done" not in event for event in tier_events)
    assert [event["tier"] for event in tier_events] == sorted(tiers)

    seen: set[str] = set()
    known = {doc["entry_id"] for doc in documents}
    for event in tier_events:
        for result in event["results"]:
            assert result["entry_id"] not in seen
            assert result["entry_id"] in known
            seen.add(result["entry_id"])


def _result_ids(event: Mapping[str, Any]) -> list[str]:
    return [result["entry_id"] for result in event["results"]]


@given(_search_case())
def test_tier_hits_imply_substring_relations_and_snippets(
    case: tuple[str, list[dict[str, Any]], list[dict[str, Any]], tuple[int, ...]],
) -> None:
    query, documents, fts, _ = case
    by_id = {doc["entry_id"]: doc for doc in documents}
    normalized = query.strip()
    events = {
        event["tier"]: event
        for event in tiered_search(query, documents, fts, tiers=(1, 2))
        if "tier" in event
    }
    for result in events[1]["results"]:
        corpus = _corpus(by_id[result["entry_id"]])
        assert normalized and normalized in corpus
        assert normalized in result["snippet"]
    for result in events[2]["results"]:
        corpus = _corpus(by_id[result["entry_id"]])
        assert normalized.casefold() in corpus.casefold()
        assert normalized.casefold() in result["snippet"].casefold()
    # Anything exactly matched is, by definition, also a substring match, so tier 1 claims it.
    exact_ids = set(_result_ids(events[1]))
    assert exact_ids == {
        d["entry_id"] for d in documents if normalized and normalized in _corpus(d)
    }
    assert exact_ids.isdisjoint(_result_ids(events[2]))


@given(
    st.text(_STABLE_CHARS, max_size=300),
    st.text(_STABLE_CHARS, min_size=1, max_size=12).filter(lambda s: bool(s.strip())),
    st.text(_STABLE_CHARS, max_size=300),
    st.booleans(),
)
def test_snippet_contains_match_for_length_stable_casefold(
    before: str, needle: str, after: str, vary_case: bool
) -> None:
    query = needle.strip()
    shown = query.swapcase() if vary_case else query
    document = {"entry_id": "e", "text": before + " " + shown + " " + after}
    events = [e for e in tiered_search(query, [document], [], tiers=(2,)) if "tier" in e]
    (result,) = events[0]["results"]
    assert query.casefold() in result["snippet"].casefold()


@given(
    st.integers(min_value=0, max_value=250),
    st.sampled_from(["ß", "ﬃ", "İ", "ŉ", "a"]),
    st.sampled_from(["needle", "NEEDLE", "Straße", "STRASSE"]),
)
def test_snippet_contains_match_after_length_changing_casefolds(
    count: int, filler: str, shown: str
) -> None:
    # Regression: the match was located in the casefolded corpus but the snippet was cut
    # from the original at that index, so fillers whose casefold is longer pushed the
    # snippet window past the match.
    query = shown.swapcase()
    document = {"entry_id": "e", "text": filler * count + " " + shown + " " + filler * count}
    events = [e for e in tiered_search(query, [document], [], tiers=(2,)) if "tier" in e]
    (result,) = events[0]["results"]
    assert query.casefold() in result["snippet"].casefold()


_ids = st.sampled_from([f"id{i}" for i in range(8)])
_rankings = st.lists(
    st.lists(_ids.map(lambda i: {"entry_id": i}), max_size=10), min_size=0, max_size=4
)


def _model_scores(rankings: Sequence[Sequence[Mapping[str, Any]]], k: int) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        first_rank: dict[str, int] = {}
        for rank, item in enumerate(ranking, start=1):
            first_rank.setdefault(item["entry_id"], rank)
        for entry_id, rank in first_rank.items():
            scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (k + rank)
    return scores


@given(_rankings, st.integers(1, 100))
def test_rrf_scores_and_order(rankings: list[list[dict[str, Any]]], k: int) -> None:
    fused = reciprocal_rank_fusion(rankings, k=k)
    expected = _model_scores(rankings, k)
    assert {i for i, _ in fused} == set(expected)
    assert len(fused) == len(expected)
    for entry_id, score in fused:
        assert math.isclose(score, expected[entry_id], rel_tol=1e-9)
    keys = [(-score, entry_id) for entry_id, score in fused]
    assert keys == sorted(keys)


@given(_rankings, st.randoms(use_true_random=False), st.integers(1, 100))
def test_rrf_is_invariant_to_ranking_list_order(
    rankings: list[list[dict[str, Any]]], rng: Any, k: int
) -> None:
    shuffled = list(rankings)
    rng.shuffle(shuffled)
    first = dict(reciprocal_rank_fusion(rankings, k=k))
    second = dict(reciprocal_rank_fusion(shuffled, k=k))
    assert first.keys() == second.keys()
    for entry_id, score in first.items():
        assert math.isclose(score, second[entry_id], rel_tol=1e-9)


@given(_rankings, st.integers(1, 100))
def test_rrf_repeats_appended_to_a_ranking_count_once(
    rankings: list[list[dict[str, Any]]], k: int
) -> None:
    padded = [ranking + ranking for ranking in rankings]
    assert reciprocal_rank_fusion(padded, k=k) == reciprocal_rank_fusion(rankings, k=k)


@settings(max_examples=25)
@given(
    st.lists(
        st.text(
            st.characters(codec="utf-8", exclude_characters="\x00"), min_size=1, max_size=40
        ).filter(lambda s: bool(s.strip())),
        min_size=1,
        max_size=4,
    ),
    st.integers(1, 6),
    st.sampled_from(["seed", "alpha", "journal entry", "x"]),
)
def test_retrieve_api_items_bounded_unique_and_ranked(
    api: Any, texts: list[str], k: int, query: str
) -> None:
    for text in texts:
        created = api.client.post("/api/v1/entries", headers=api.headers, json={"text": text})
        assert created.status_code == 201
    response = api.client.post(
        "/api/v1/retrieve", headers=api.headers, json={"query": query, "k": k}
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) <= k
    ids = [item["entry"]["id"] for item in items]
    assert len(ids) == len(set(ids))
    scores = [item["score"] for item in items]
    assert scores == sorted(scores, reverse=True)
