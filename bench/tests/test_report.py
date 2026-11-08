import csv
import json
import random

import pytest

from msj_bench.report import compare_runs, comparison_table, summarize, write_json, write_rating_sheets
from msj_bench.runpod_client import BenchmarkError


def record(item_id="one", output="candidate", source="shared source"):
    return {"item_id": item_id, "task": "cleanup", "source": source, "output": output,
            "status": "ok", "server_model": "private-model", "metrics": {"reference_wer": 0.1},
            "performance": {"wall_time_s": 2, "estimated_cost_usd": None}}


def test_anonymization_and_separate_key(tmp_path):
    runs = [({"label": f"secret-label-{i}", "run_id": f"run-{i}"},
             [record(output=f"output {i}"), record(item_id="two", output=f"second {i}")]) for i in range(3)]
    write_rating_sheets(runs, tmp_path, rng=random.Random(3))
    raw = (tmp_path / "ratings-cleanup.csv").read_text()
    assert "secret-label" not in raw and "private-model" not in raw and "run-0" not in raw
    rows = list(csv.DictReader(raw.splitlines()))
    keys = json.loads((tmp_path / "ratings-cleanup-key.json").read_text())
    assert len(rows) == len(keys) == 6
    assert len({row["anonymous_id"] for row in rows}) == 6
    aliases = {key["anonymous_id"]: key for key in keys}
    for row in rows:
        assert row["anonymous_id"] in aliases
        assert row["faithfulness_1_5"] == row["fluency_1_5"] == row["usefulness_1_5"] == ""
    assert {key["label"] for key in keys} == {f"secret-label-{i}" for i in range(3)}


def test_aggregation_errors_and_missing_cost(tmp_path):
    records = [record(), {"task": "cleanup", "item_id": "bad", "status": "error", "source": "other", "error": "failed"}]
    summary = summarize(records, {"label": "A|B", "run_id": "run"})
    task = summary["tasks"]["cleanup"]
    assert task["items"] == 2 and task["failures"] == 1
    assert task["estimated_cost_usd_total"] is None
    assert task["means"]["wall_time_s"] == 2
    assert "A\\|B" in comparison_table([summary])
    run = tmp_path / "input"
    run.mkdir()
    write_json(run / "summary.json", summary)
    _written = (run / "results.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    out = tmp_path / "comparison"
    table = compare_runs([run], out)
    assert "1/2" in table
    assert (out / "ratings-cleanup.csv").is_file()
    with pytest.raises(BenchmarkError, match="only once"):
        compare_runs([run, run], out)


def test_reject_mismatched_sources(tmp_path):
    with pytest.raises(BenchmarkError, match="different source"):
        write_rating_sheets([({"label": "a"}, [record()]), ({"label": "b"}, [record(source="changed")])], tmp_path)


def test_failed_task_still_has_an_empty_sheet(tmp_path):
    write_rating_sheets([({"label": "a", "requested_tasks": ["cleanup"]}, [])], tmp_path)
    with (tmp_path / "ratings-cleanup.csv").open() as handle:
        assert list(csv.DictReader(handle)) == []
    assert json.loads((tmp_path / "ratings-cleanup-key.json").read_text()) == []
