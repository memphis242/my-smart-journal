"""Text and audio entries go through a forced batch on a real `msj worker` to done."""

import io
import wave
from collections import Counter
from typing import Any

import pytest

from .msj_harness import MsjInstance

pytestmark = pytest.mark.integration


def _wav_bytes() -> bytes:
    """A tiny valid mono WAV; the fake transcriber does not decode it."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x01" * 800)
    return buffer.getvalue()


def _current_kinds(entry: dict[str, Any]) -> Counter[str]:
    return Counter(
        artifact["kind"]
        for artifact in entry["artifacts"]
        if artifact["revision"] == entry["revision"] and artifact["superseded_by"] is None
    )


def test_text_and_audio_entries_are_processed_to_done(msj: MsjInstance) -> None:
    text = msj.create_text_entry(
        "Shipped the Atlas ingestion fix and paired with Dana on retries.",
        title="Atlas fix",
        tags=["work"],
        entry_date="2026-11-06",
    )
    assert text["status"] == "queued"
    assert text["version"] == 1
    assert {job["kind"] for job in text["jobs"]} >= {"cleanup", "summarize", "extract"}

    wav = _wav_bytes()
    audio = msj.expect(
        msj.api.post(
            "/api/v1/entries",
            files={"file": ("memo.wav", wav, "audio/wav")},
            data={"title": "Voice memo", "tags": '["voice"]', "entry_date": "2026-11-07"},
        ),
        201,
    )
    assert audio["source"] == "audio"
    assert {job["kind"] for job in audio["jobs"]} >= {"transcribe", "cleanup"}

    msj.process_entries([text["id"], audio["id"]])

    for entry_id, source_kinds in (
        (text["id"], {"user_text"}),
        (audio["id"], {"raw_audio", "raw_transcript"}),
    ):
        entry = msj.entry(entry_id)
        assert entry["status"] == "done", entry
        kinds = _current_kinds(entry)
        for kind in (*source_kinds, "cleaned", "summary", "extraction"):
            assert kinds[kind] == 1, f"{entry_id}: expected one current {kind}, got {kinds}"
        assert entry["summary"], entry
        assert entry["text"], entry
        assert all(job["status"] == "done" for job in entry["jobs"]), entry["jobs"]
        assert {
            target: session["status"] for target, session in entry["review_sessions"].items()
        } == {"cleanup": "accepted", "summarize": "accepted", "extract": "accepted"}
        produced = [a for a in entry["artifacts"] if a["kind"] in {"cleaned", "summary"}]
        assert all(artifact["model"] == "fake" for artifact in produced), produced

    detail = msj.entry(text["id"])
    assert detail["title"] == "Atlas fix"
    assert detail["tags"] == ["work"]
    assert detail["entry_date"] == "2026-11-06"
    assert detail["text"] == "Shipped the Atlas ingestion fix and paired with Dana on retries."

    recording = msj.entry(audio["id"])
    raw_audio = next(a for a in recording["artifacts"] if a["kind"] == "raw_audio")
    assert raw_audio["content_type"] == "audio/wav"
    assert (msj.data_dir / raw_audio["content"]).read_bytes() == wav
    assert recording["title"] == "Voice memo"
    assert recording["tags"] == ["voice"]

    queue = msj.queue()
    assert queue["counts"] == {"queued": 0, "running": 0, "failed": 0, "blocked": 0}
    assert queue["batch"]["last_error"] is None
    assert not queue["worker"]["stale"]
