"""Filtered entry views and facet aggregation for the API."""

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any


def summary_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Return compact fields derived from the current artifacts and jobs."""
    revision = entry.get("revision", 1)
    artifacts = [
        item
        for item in entry.get("artifacts", [])
        if item.get("revision") == revision and item.get("superseded_by") is None
    ]

    cleaned = final_artifact(entry, "cleaned")
    if cleaned is None:
        cleaned = next(
            (
                item
                for item in reversed(artifacts)
                if item.get("kind") in {"raw_transcript", "user_text"}
            ),
            None,
        )
    summary = final_artifact(entry, "summary")
    extraction = final_artifact(entry, "extraction")
    try:
        extraction_data = json.loads(extraction["content"]) if extraction else {}
    except ValueError, TypeError:
        extraction_data = {}
    if not isinstance(extraction_data, dict):
        extraction_data = {}
    jobs = entry.get("jobs", [])
    statuses = [str(job.get("status")) for job in jobs if job.get("revision") == revision]
    if "failed" in statuses or any(
        job.get("blocked_by") for job in jobs if job.get("revision") == revision
    ):
        status = "failed"
    elif "running" in statuses:
        status = "processing"
    elif "queued" in statuses:
        status = "queued"
    elif entry.get("needs_attention"):
        status = "needs_attention"
    else:
        status = "done"
    return {
        "summary": summary.get("content") if summary else None,
        "text": cleaned.get("content", "") if cleaned else "",
        "projects": _string_list(extraction_data.get("projects")),
        "skills": _string_list(extraction_data.get("skills_technologies")),
        "status": status,
        "revision": revision,
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def final_artifact(entry: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """Return the published current-revision artifact for a reviewed output kind."""
    targets = {"cleaned": "cleanup", "summary": "summarize", "extraction": "extract"}
    target = targets.get(kind)
    if target is None:
        raise ValueError(f"Unsupported final artifact kind: {kind}")
    session = entry.get("review_sessions", {}).get(target)
    if isinstance(session, dict):
        if session.get("status") == "pending":
            return None
        artifact = session.get("final_artifact")
        if isinstance(artifact, dict) and artifact.get("kind") == kind:
            return artifact
        if session.get("status") != "disabled":
            return None
    revision = entry.get("revision")
    return next(
        (
            item
            for item in reversed(entry.get("artifacts", []))
            if item.get("kind") == kind
            and item.get("revision") == revision
            and item.get("superseded_by") is None
        ),
        None,
    )


def filter_entries(
    entries: Iterable[dict[str, Any]],
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    tags: list[str] | None = None,
    projects: list[str] | None = None,
    skills: list[str] | None = None,
    order: str = "desc",
) -> list[dict[str, Any]]:
    lower = date.fromisoformat(from_date).isoformat() if from_date else None
    upper = date.fromisoformat(to_date).isoformat() if to_date else None
    if (from_date is not None and lower != from_date) or (to_date is not None and upper != to_date):
        raise ValueError("Date bounds must use YYYY-MM-DD")
    wanted = tuple((values or []) for values in (tags, projects, skills))
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        entry_date = str(entry["entry_date"])
        if lower is not None and entry_date < lower or upper is not None and entry_date > upper:
            continue
        facets = (entry.get("tags", []), entry.get("projects", []), entry.get("skills", []))
        if any(
            values
            and not {str(x).casefold() for x in values}.intersection(
                str(x).casefold() for x in facet
            )
            for values, facet in zip(wanted, facets, strict=True)
        ):
            continue
        filtered.append(entry)
    filtered.sort(
        key=lambda item: (item["entry_date"], item["created_at"], item["id"]),
        reverse=order == "desc",
    )
    return filtered


def facet_values(entries: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    counts: dict[str, Counter[str]] = {
        "tags": Counter(),
        "projects": Counter(),
        "skills": Counter(),
    }
    spellings: dict[str, dict[str, Counter[str]]] = {key: {} for key in counts}
    for entry in entries:
        for field in counts:
            values = entry.get(field, [])
            grouped: dict[str, set[str]] = {}
            for raw in set(str(value) for value in values):
                grouped.setdefault(raw.casefold(), set()).add(raw)
            for folded, variants in grouped.items():
                counts[field][folded] += 1
                spellings_for_value = spellings[field].setdefault(folded, Counter())
                for raw in variants:
                    spellings_for_value[raw] += 1
    return {
        field: [
            {"value": _preferred_spelling(spellings[field][folded]), "count": count}
            for folded, count in sorted(
                counts[field].items(),
                key=lambda pair: (
                    -pair[1],
                    _preferred_spelling(spellings[field][pair[0]]).casefold(),
                    _preferred_spelling(spellings[field][pair[0]]),
                ),
            )
        ]
        for field in counts
    }


def summary_item(entry: dict[str, Any]) -> dict[str, Any]:
    fields = summary_fields(entry)
    return {
        key: entry.get(key) for key in ("id", "entry_date", "created_at", "title", "tags", "source")
    } | fields


def export_item(entry: dict[str, Any]) -> dict[str, Any]:
    extraction = final_artifact(entry, "extraction")
    try:
        extraction_data = json.loads(extraction["content"]) if extraction else None
    except ValueError, TypeError:
        extraction_data = None
    if not isinstance(extraction_data, dict):
        extraction_data = None
    return (
        {
            key: entry.get(key)
            for key in ("id", "entry_date", "created_at", "title", "tags", "source")
        }
        | summary_fields(entry)
        | {"extraction": extraction_data}
    )


def _preferred_spelling(spellings: Counter[str]) -> str:
    return min(spellings, key=lambda value: (-spellings[value], value.casefold(), value))
