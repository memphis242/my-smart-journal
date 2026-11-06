import json

import pytest

from msj_bench.datasets import BENCH_ROOT, load_dataset
from msj_bench.metrics import normalize
from msj_bench.runpod_client import BenchmarkError


def test_synthetic_fixtures_are_fictional_and_well_formed():
    items = load_dataset("synthetic")
    assert len(items) == 12
    for item in items:
        value = json.loads((BENCH_ROOT / "data" / "synthetic" / f"{item.id}.json").read_text())
        assert value["fictional"] is True
        assert 80 <= len(item.raw_transcript.split()) <= 400
        assert item.reference_cleaned and item.key_facts
        assert item.raw_transcript != item.reference_cleaned
        raw_terms = set(normalize(item.raw_transcript).split())
        cleaned_terms = set(normalize(item.reference_cleaned).split())
        for fact in item.key_facts:
            assert set(normalize(fact).split()) <= raw_terms
            assert set(normalize(fact).split()) <= cleaned_terms
    with pytest.raises(BenchmarkError, match="text-only"):
        load_dataset("synthetic", mode="stt")


def test_paired_recording_loading(tmp_path):
    _written = (tmp_path / "one.wav").write_bytes(b"not-real-audio")
    _written = (tmp_path / "one.txt").write_text("Um, 3.5 V from Mira.")
    _written = (tmp_path / "one.cleaned.txt").write_text("3.5 V from Mira.")
    item, = load_dataset(tmp_path, mode="stt")
    assert item.id == "one"
    assert item.audio == tmp_path / "one.wav"
    assert item.reference_cleaned == "3.5 V from Mira."


@pytest.mark.parametrize("records", [
    [{"id": "same", "raw_transcript": "one"}, {"id": "same", "raw_transcript": "two"}],
    [{"id": "empty", "raw_transcript": " "}],
    [{"id": "bad", "raw_transcript": "words", "key_facts": [3]}],
])
def test_invalid_json_dataset(tmp_path, records):
    path = tmp_path / "input.json"
    _written = path.write_text(json.dumps(records))
    with pytest.raises(BenchmarkError):
        load_dataset(path)


def test_missing_reference_and_duplicate_audio_ids(tmp_path):
    _written = (tmp_path / "a.mp3").write_bytes(b"fake")
    with pytest.raises(BenchmarkError, match="verbatim transcript"):
        load_dataset(tmp_path)
    _written = (tmp_path / "a.txt").write_text("a source")
    _written = (tmp_path / "a.ogg").write_bytes(b"another fake")
    with pytest.raises(BenchmarkError, match="unique"):
        load_dataset(tmp_path)


def test_manifest_audio_paths_are_relative_and_not_duplicated(tmp_path):
    _written = (tmp_path / "record.wav").write_bytes(b"fake")
    _written = (tmp_path / "manifest.json").write_text(json.dumps({"id": "custom-id", "raw_transcript": "words", "audio": "record.wav"}))
    item, = load_dataset(tmp_path, mode="stt")
    assert item.id == "custom-id"
    assert item.audio == tmp_path / "record.wav"
