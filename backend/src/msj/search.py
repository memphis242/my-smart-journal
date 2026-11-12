"""Deterministic, streaming tiers for private journal search."""

from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from rapidfuzz.fuzz import partial_ratio, token_set_ratio


def _corpus(document: Mapping[str, Any]) -> str:
    fields = ("text", "summary", "title", "tags")
    return " ".join(str(document.get(field) or "") for field in fields)


def _snippet(document: Mapping[str, Any], query: str, *, case_sensitive: bool) -> str:
    corpus = _corpus(document).strip()
    if not corpus:
        return ""
    haystack = corpus if case_sensitive else corpus.casefold()
    needle = query if case_sensitive else query.casefold()
    position = haystack.find(needle)
    if position < 0:
        position = 0
    start = max(0, position - 80)
    end = min(len(corpus), position + len(query) + 120)
    return ("…" if start else "") + corpus[start:end] + ("…" if end < len(corpus) else "")


def _recency(created_at: Any, now: datetime) -> float:
    if not isinstance(created_at, str):
        return 0.0
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age_days = max(0.0, (now - parsed.astimezone(UTC)).total_seconds() / 86400)
    return 1.0 / (1.0 + age_days / 30.0)


def tiered_search(
    query: str,
    documents: Sequence[Mapping[str, Any]],
    fts_results: Sequence[Mapping[str, Any]] | Callable[[], Sequence[Mapping[str, Any]]],
    *,
    fuzzy_threshold: float = 80.0,
    tiers: tuple[int, ...] = (1, 2, 3, 4, 5),
    semantic: Callable[[], tuple[Sequence[Mapping[str, Any]], Mapping[str, str] | None]]
    | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield one event per completed tier, followed by a completion event."""
    normalized = query.strip()
    assert len(tiers) == len(set(tiers))
    assert all(tier in {1, 2, 3, 4, 5} for tier in tiers)
    started = monotonic()
    emitted: set[str] = set()

    def event(
        tier: int,
        name: str,
        candidates: Sequence[tuple[Mapping[str, Any], float]],
        *,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for document, score in candidates:
            entry_id = str(document["entry_id"])
            if entry_id in emitted:
                continue
            emitted.add(entry_id)
            results.append(
                {
                    "entry_id": entry_id,
                    "score": round(score, 4),
                    "snippet": _snippet(document, normalized, case_sensitive=case_sensitive),
                }
            )
        return {
            "tier": tier,
            "name": name,
            "results": results,
            "elapsed_ms": round((monotonic() - started) * 1000, 3),
        }

    if 1 in tiers:
        exact = [(doc, 1.0) for doc in documents if normalized and normalized in _corpus(doc)]
        yield event(1, "exact", exact, case_sensitive=True)

    folded_query = normalized.casefold()
    if 2 in tiers:
        substring = [
            (doc, 1.0)
            for doc in documents
            if folded_query and folded_query in _corpus(doc).casefold()
        ]
        yield event(2, "substring", substring)

    if 3 in tiers:
        fuzzy: list[tuple[Mapping[str, Any], float]] = []
        if normalized:
            for doc in documents:
                content = _corpus(doc)
                score = float(
                    max(
                        partial_ratio(folded_query, content.casefold()),
                        token_set_ratio(folded_query, content.casefold()),
                    )
                )
                if score >= fuzzy_threshold:
                    fuzzy.append((doc, score / 100.0))
        fuzzy.sort(key=lambda item: (-item[1], str(item[0]["entry_id"])))
        yield event(3, "fuzzy", fuzzy)

    if 4 in tiers:
        by_id = {str(doc["entry_id"]): doc for doc in documents}
        now = datetime.now(UTC)
        fts_rows = fts_results() if callable(fts_results) else fts_results
        raw_ranked: list[tuple[Mapping[str, Any], float]] = []
        for result in fts_rows:
            entry_id = str(result["entry_id"])
            document = by_id.get(entry_id)
            if document is None:
                continue
            rank = result.get("rank", 0.0)
            try:
                relevance = max(0.0, -float(rank))
            except TypeError, ValueError:
                relevance = 0.0
            raw_ranked.append((document, relevance))
        strongest = max(1e-12, max((score for _, score in raw_ranked), default=1.0))
        ranked = [
            (document, score / strongest + 0.1 * _recency(document.get("created_at"), now))
            for document, score in raw_ranked
        ]
        ranked.sort(key=lambda item: (-item[1], str(item[0]["entry_id"])))
        yield event(4, "deterministic", ranked)

    if 5 in tiers:
        semantic_rows, unavailable = (
            semantic()
            if semantic is not None
            else (
                (),
                {
                    "code": "EMBEDDINGS_DISABLED",
                    "detail": "Semantic search is unavailable because no embedder is configured.",
                },
            )
        )
        results: list[dict[str, Any]] = []
        for result in semantic_rows:
            entry_id = str(result["entry_id"])
            if entry_id in emitted:
                continue
            emitted.add(entry_id)
            results.append(
                {
                    "entry_id": entry_id,
                    "score": round(float(result["score"]), 4),
                    "snippet": str(result["passage"]),
                    "passage": str(result["passage"]),
                }
            )
        semantic_event: dict[str, Any] = {
            "tier": 5,
            "name": "semantic",
            "results": results,
            "elapsed_ms": round((monotonic() - started) * 1000, 3),
        }
        if unavailable is not None:
            semantic_event["unavailable"] = dict(unavailable)
        yield semantic_event
    yield {"done": True, "elapsed_ms": round((monotonic() - started) * 1000, 3)}


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Mapping[str, Any]]], *, k: int = 60
) -> list[tuple[str, float]]:
    """Combine ordered result lists while retaining a stable score order."""
    assert k > 0
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, item in enumerate(ranking, start=1):
            entry_id = str(item["entry_id"])
            if entry_id not in seen:
                scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (k + rank)
                seen.add(entry_id)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
