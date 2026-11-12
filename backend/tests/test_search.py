"""Behavioral coverage for ordered tiered search and deduplication."""

from datetime import UTC, datetime
from typing import Any

from msj.search import reciprocal_rank_fusion, tiered_search


def test_search_emits_four_tiers_then_done_and_deduplicates() -> None:
    documents: list[dict[str, Any]] = [
        {
            "entry_id": "exact",
            "text": "We shipped Atlas today",
            "summary": "",
            "title": None,
            "tags": [],
            "created_at": datetime.now(UTC).isoformat(),
        },
        {
            "entry_id": "folded",
            "text": "atlas was discussed",
            "summary": "",
            "title": None,
            "tags": [],
            "created_at": "bad date",
        },
        {
            "entry_id": "fuzzy",
            "text": "the quarterly milestones are done",
            "summary": "",
            "title": None,
            "tags": [],
            "created_at": "bad date",
        },
        {
            "entry_id": "fts",
            "text": "unrelated note",
            "summary": "",
            "title": None,
            "tags": [],
            "created_at": "bad date",
        },
    ]
    events = list(
        tiered_search(
            "Atlas",
            documents,
            [{"entry_id": "exact", "rank": -1.0}, {"entry_id": "fts", "rank": -0.2}],
            fuzzy_threshold=75,
        )
    )
    assert [event.get("tier") for event in events[:-1]] == [1, 2, 3, 4, 5]
    assert events[-1]["done"] is True
    ids = [[item["entry_id"] for item in event["results"]] for event in events[:-1]]
    assert ids[0] == ["exact"]
    assert ids[1] == ["folded"]
    assert "fts" in ids[3]
    flattened = [entry_id for tier in ids for entry_id in tier]
    assert len(flattened) == len(set(flattened))


def test_exact_tier_preserves_case_and_empty_tiers_still_stream() -> None:
    documents: list[dict[str, Any]] = [
        {
            "entry_id": "1",
            "text": "Plan a garden",
            "summary": "",
            "title": None,
            "tags": [],
            "created_at": "",
        }
    ]
    events = list(tiered_search("Garden", documents, []))
    assert events[0]["results"] == []
    assert len(events) == 6
    assert all("elapsed_ms" in event for event in events)


def test_fts_is_deferred_until_the_fourth_event() -> None:
    documents: list[dict[str, Any]] = [
        {
            "entry_id": "1",
            "text": "Alpha note",
            "summary": "",
            "title": None,
            "tags": [],
            "created_at": "",
        }
    ]
    calls: list[str] = []

    def fetch_fts() -> list[dict[str, object]]:
        calls.append("fts")
        return []

    stream = tiered_search("missing", documents, fetch_fts)
    assert next(stream)["tier"] == 1
    assert calls == []
    assert next(stream)["tier"] == 2
    assert next(stream)["tier"] == 3
    assert calls == []
    assert next(stream)["tier"] == 4
    assert calls == ["fts"]


def test_semantic_tier_can_be_selected_and_deduplicates_prior_results() -> None:
    documents: list[dict[str, Any]] = [
        {
            "entry_id": "lexical",
            "text": "We shipped Atlas",
            "summary": "",
            "title": None,
            "tags": [],
            "created_at": "",
        }
    ]
    calls: list[str] = []

    def semantic() -> tuple[list[dict[str, object]], None]:
        calls.append("semantic")
        return (
            [
                {"entry_id": "lexical", "score": 0.8, "passage": "same entry"},
                {"entry_id": "semantic", "score": 0.7, "passage": "related passage"},
            ],
            None,
        )

    events = list(tiered_search("Atlas", documents, [], tiers=(1, 5), semantic=semantic))
    assert calls == ["semantic"]
    assert [event.get("tier") for event in events[:-1]] == [1, 5]
    assert [item["entry_id"] for item in events[1]["results"]] == ["semantic"]
    assert events[1]["results"][0]["passage"] == "related passage"


def test_reciprocal_rank_fusion_combines_lists_and_is_stable() -> None:
    ranked = reciprocal_rank_fusion(
        [
            [{"entry_id": "a"}, {"entry_id": "b"}],
            [{"entry_id": "b"}, {"entry_id": "c"}],
        ],
        k=10,
    )
    assert [entry_id for entry_id, _score in ranked] == ["b", "a", "c"]


def test_real_fuzzy_match_and_sqlite_fts_recency(tmp_path) -> None:
    from msj.config import Settings
    from msj.storage import Store

    store = Store(Settings(data_dir=tmp_path))
    store.migrate()
    fuzzy_entry = store.create_entry(text="I completed the Atlaz migration.")
    stream = list(tiered_search("Atlas", store.search_documents(), []))
    assert stream[0]["results"] == []
    assert stream[1]["results"] == []
    assert stream[2]["results"][0]["entry_id"] == fuzzy_entry["id"]

    old = store.create_entry(text="I planted oak in a pot.", title="Garden tools")
    recent = store.create_entry(text="I planted oak in a pot.", title="Garden tools")
    with store.connect() as db:
        _ = db.execute(
            "UPDATE entries SET created_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", old["id"])
        )
    events = list(
        tiered_search(
            "oak orchard",
            store.search_documents(),
            lambda: store.fts_search("oak orchard"),
            fuzzy_threshold=100,
        )
    )
    ranked = [item["entry_id"] for item in events[3]["results"]]
    assert ranked == [recent["id"], old["id"]]
    assert store.fts_search('" OR (') == []
    store.delete_entry(recent["id"])
    assert recent["id"] not in {item["entry_id"] for item in store.fts_search("oak")}
