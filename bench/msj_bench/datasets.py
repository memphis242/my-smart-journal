"""Strict, immutable dataset loading without reading unrelated private files."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Final

from .runpod_client import BenchmarkError

BENCH_ROOT: Final = Path(__file__).resolve().parents[1]
AUDIO_SUFFIXES: Final = frozenset({".wav", ".m4a", ".mp3", ".ogg"})


@dataclass(frozen=True)
class JournalItem:
    id: str
    raw_transcript: str
    reference_cleaned: str | None = None
    key_facts: tuple[str, ...] = ()
    audio: Path | str | None = None


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"Dataset field {field} must be a nonempty string")
    return value


def _json_items(path: Path) -> list[JournalItem]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BenchmarkError(f"Cannot read valid dataset JSON: {path.name}") from None
    records = data if isinstance(data, list) else [data]
    items: list[JournalItem] = []
    for entry in records:
        if not isinstance(entry, dict):
            raise BenchmarkError("Each dataset entry must be an object")
        item_id = _text(entry.get("id"), "id")
        raw = _text(entry.get("raw_transcript"), "raw_transcript")
        reference = entry.get("reference_cleaned")
        if reference is not None:
            reference = _text(reference, "reference_cleaned")
        facts = entry.get("key_facts", [])
        if not isinstance(facts, list) or not all(isinstance(v, str) and v.strip() for v in facts):
            raise BenchmarkError("key_facts must be a list of nonempty strings")
        audio_value = entry.get("audio")
        audio: Path | str | None = None
        if audio_value is not None:
            value = _text(audio_value, "audio")
            audio = value if value.startswith(("http://", "https://")) else (path.parent / value).resolve()
            if isinstance(audio, Path) and not audio.is_file():
                raise BenchmarkError(f"Missing audio file for item {item_id}")
        items.append(JournalItem(item_id, raw, reference, tuple(facts), audio))
    return items


def load_dataset(dataset: str | Path, *, mode: str = "llm") -> tuple[JournalItem, ...]:
    path = BENCH_ROOT / "data" / str(dataset) if str(dataset) in {"synthetic", "user"} else Path(dataset)
    if not path.exists():
        raise BenchmarkError(f"Dataset does not exist: {path}")
    items: list[JournalItem] = []
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    for file in files:
        items.extend(_json_items(file))
    if path.is_dir():
        audios = sorted(p for p in path.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES)
        existing_audio = {item.audio for item in items if isinstance(item.audio, Path)}
        for audio in audios:
            if audio.resolve() in existing_audio:
                continue
            reference_path = audio.with_suffix(".txt")
            if not reference_path.is_file():
                raise BenchmarkError(f"Missing verbatim transcript {reference_path.name}")
            try:
                raw = _text(reference_path.read_text(encoding="utf-8"), "reference transcript")
            except OSError:
                raise BenchmarkError(f"Cannot read transcript {reference_path.name}") from None
            cleaned_path = audio.with_suffix(".cleaned.txt")
            try:
                cleaned = _text(cleaned_path.read_text(encoding="utf-8"), "reference_cleaned") if cleaned_path.exists() else None
            except OSError:
                raise BenchmarkError(f"Cannot read cleaned reference {cleaned_path.name}") from None
            items.append(JournalItem(audio.stem, raw, cleaned, (), audio.resolve()))
    if not items:
        raise BenchmarkError("Dataset contains no entries")
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise BenchmarkError("Dataset item IDs must be unique (including recording stems)")
    if mode == "stt" and any(item.audio is None for item in items):
        raise BenchmarkError("STT requires audio for every item; synthetic entries are text-only. Use paired user recordings or JSON audio fields")
    assert len(items) == len(set(ids))
    return tuple(items)
