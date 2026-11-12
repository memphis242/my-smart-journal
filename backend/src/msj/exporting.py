"""Journal export renderers for text, Markdown, and JSON."""

import json
from typing import Any


def render(
    entries: list[dict[str, Any]], *, output_format: str, include: dict[str, bool], order: str
) -> tuple[bytes, str, str]:
    ordered = sorted(
        entries,
        key=lambda item: (item["entry_date"], item["created_at"], item["id"]),
        reverse=order == "desc",
    )
    if output_format == "json":
        data = [_json_entry(item, include) for item in ordered]
        return json.dumps(data, ensure_ascii=False, indent=2).encode(), "application/json", "json"
    blocks = [
        _markdown_entry(item, include)
        if output_format == "markdown"
        else _text_entry(item, include)
        for item in ordered
    ]
    if output_format == "markdown":
        return "\n\n---\n\n".join(blocks).encode(), "text/markdown", "md"
    return "\n\n--------------------\n\n".join(blocks).encode(), "text/plain; charset=utf-8", "txt"


def _json_entry(item: dict[str, Any], include: dict[str, bool]) -> dict[str, Any]:
    result: dict[str, Any] = {"id": item["id"]}
    for flag, key in (
        ("date", "entry_date"),
        ("title", "title"),
        ("tags", "tags"),
        ("summary", "summary"),
        ("text", "text"),
        ("extraction", "extraction"),
    ):
        if (
            include.get(flag, flag in {"date", "title", "tags", "summary", "text"})
            and item.get(key) is not None
        ):
            result[key] = item[key]
    return result


def _header(item: dict[str, Any], include: dict[str, bool], markdown: bool) -> list[str]:
    date = str(item["entry_date"]) if include.get("date", True) else ""
    title = str(item.get("title") or "") if include.get("title", True) else ""
    text = "  ".join(value for value in (date, title) if value)
    if markdown and text:
        text = "# " + text
    return [text] if text else []


def _extraction_lines(item: dict[str, Any], markdown: bool) -> list[str]:
    extraction = item.get("extraction")
    if not isinstance(extraction, dict):
        return []
    lines: list[str] = []
    fields = (
        ("Projects", "projects"),
        ("Skills and technologies", "skills_technologies"),
        ("Accomplishments", "accomplishments"),
        ("People and roles", "people_roles"),
        ("STAR stories", "star_fragments"),
        ("Tags", "tags"),
    )
    for label, key in fields:
        values = extraction.get(key)
        if not isinstance(values, list) or not values:
            continue
        lines.extend(("", ("## " if markdown else "") + label + ":"))
        for value in values:
            lines.extend(_flatten(value, level=0, markdown=markdown))
    return lines


def _flatten(value: Any, *, level: int, markdown: bool, label: str | None = None) -> list[str]:
    indent = "  " * level
    prefix = "- " if markdown else ""
    if isinstance(value, dict):
        lines: list[str] = []
        if label is not None:
            lines.append(indent + prefix + label + ":")
            level += 1
        for key, child in value.items():
            lines.extend(_flatten(child, level=level, markdown=markdown, label=str(key)))
        return lines
    if isinstance(value, list):
        lines = []
        if label is not None:
            lines.append(indent + prefix + label + ":")
            level += 1
        for child in value:
            lines.extend(_flatten(child, level=level, markdown=markdown))
        return lines
    if isinstance(value, (str, int, float)):
        text = f"{label}: {value}" if label is not None else str(value)
        return [indent + prefix + text]
    return []


def _markdown_entry(item: dict[str, Any], include: dict[str, bool]) -> str:
    lines = _header(item, include, True)
    if include.get("summary", True) and item.get("summary"):
        lines.extend(("", "## Summary", str(item["summary"])))
    if include.get("text", True) and item.get("text"):
        lines.extend(("", "## Text", str(item["text"])))
    if include.get("tags", True) and item.get("tags"):
        lines.extend(("", "Tags: " + ", ".join(item["tags"])))
    if include.get("extraction", False):
        lines.extend(_extraction_lines(item, True))
    return "\n".join(lines)


def _text_entry(item: dict[str, Any], include: dict[str, bool]) -> str:
    lines = _header(item, include, False)
    if include.get("summary", True) and item.get("summary"):
        lines.extend(("", "Summary:", str(item["summary"])))
    if include.get("text", True) and item.get("text"):
        lines.extend(("", str(item["text"])))
    if include.get("tags", True) and item.get("tags"):
        lines.extend(("", "Tags:", ", ".join(item["tags"])))
    if include.get("extraction", False):
        lines.extend(_extraction_lines(item, False))
    return "\n".join(lines)
