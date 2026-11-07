import json
from pathlib import Path

from msj_bench.datasets import BENCH_ROOT, load_compile_dataset, load_dataset
from msj_bench.metrics import compile_metrics
from msj_bench.tasks import compile_schema


def test_all_synthetic_sets_load_and_are_fictional():
    for name in ("synthetic", "synthetic-long"):
        items = load_dataset(name)
        for item in items:
            row = json.loads((BENCH_ROOT / "data" / name / f"{item.id}.json").read_text())
            assert row["fictional"] is True
            assert item.key_facts
    for path in (BENCH_ROOT / "data" / "compile").glob("*.json"):
        row = json.loads(path.read_text())
        assert row["fictional"] is True
        assert all(80 <= len(entry["text"].split()) <= 300 for entry in row["entries"])
    assert len(load_compile_dataset("compile")) == 3


def test_long_entries_have_requested_context_length():
    for item in load_dataset("synthetic-long"):
        assert 450 <= len(item.raw_transcript.split()) <= 900
        assert len(item.key_facts) >= 15


def test_compile_metrics_dates_facts_and_unfinished_leaks():
    item = load_compile_dataset("compile")[0]
    output = json.dumps({"resume_bullets":[{"text":"Recorded Cedar Metrics 14 ms p95 over 20 minutes", "evidence_dates":["2026-10-02"]}],
                         "star_stories":[], "technical_topics":[], "open_threads":["Deployment remains open"]})
    metrics = compile_metrics(output, item, compile_schema())
    assert metrics["schema_valid"] and metrics["evidence_dates_valid"]
    assert metrics["key_fact_recall"] > 0
    assert metrics["not_accomplished_leaks"] == 0
    invalid = json.loads(output)
    invalid["resume_bullets"][0]["evidence_dates"] = ["2026-12-31"]
    assert not compile_metrics(json.dumps(invalid), item, compile_schema())["evidence_dates_valid"]


def test_negated_claims_do_not_leak_and_labels_are_not_names():
    from msj_bench.datasets import load_compile_dataset
    item = load_compile_dataset("compile")[0]
    phrase = item.not_accomplished[0]
    output = {"resume_bullets": [{"text": f"Work was {phrase}? No: it was not {phrase}.", "evidence_dates": []}],
              "star_stories": [{"title": "Zyxwv Story", "situation": "", "task": "", "action": "", "result": "",
                                "evidence_dates": [], "competencies": ["Qwertyuiop"]}],
              "technical_topics": [], "open_threads": []}
    metrics = compile_metrics(json.dumps(output), item, compile_schema())
    assert "Qwertyuiop" not in metrics["unsupported_names"]
    assert "Zyxwv" not in metrics["unsupported_names"]
    affirmative = dict(output, resume_bullets=[{"text": f"Successfully {phrase}.", "evidence_dates": []}])
    assert compile_metrics(json.dumps(affirmative), item, compile_schema())["not_accomplished_leaks"] >= 1


def test_compile_schema_restricts_evidence_dates():
    from jsonschema import Draft202012Validator
    from msj_bench.tasks import compile_schema_for
    schema = compile_schema_for(["2026-10-02", "2026-10-06"])
    bad = {"resume_bullets": [{"text": "x", "evidence_dates": ["2026-9-21"]}], "star_stories": [],
           "technical_topics": [], "open_threads": []}
    assert list(Draft202012Validator(schema).iter_errors(bad))
