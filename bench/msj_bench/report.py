"""Per-task aggregates and blind, randomized human-rating packets."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
import csv
import json
from pathlib import Path
import random
from statistics import fmean
from typing import Any, Final
import uuid

from .runpod_client import BenchmarkError

RATING_COLUMNS: Final = ("item_id", "anonymous_id", "source", "review_candidate", "output",
                         "faithfulness_1_5", "fluency_1_5", "usefulness_1_5")


def write_json(path: Path, value: Any) -> None:
    _written = path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


def summarize(records: Sequence[Mapping[str, Any]], metadata: Mapping[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["task"]].append(record)
    tasks: dict[str, Any] = {}
    for task, items in sorted(groups.items()):
        good = [r for r in items if r["status"] == "ok"]
        values: dict[str, list[float]] = defaultdict(list)
        for record in good:
            for key, value in {**record["metrics"], **record["performance"]}.items():
                if isinstance(value, (int, float)):
                    values[key].append(float(value))
        tasks[task] = {"items": len(items), "successes": len(good), "failures": len(items) - len(good),
                       "means": {key: fmean(v) for key, v in sorted(values.items())},
                       "metric_samples": {key: len(v) for key, v in sorted(values.items())},
                       "estimated_cost_usd_total": sum(values["estimated_cost_usd"]) if values["estimated_cost_usd"] else None}
    return {**metadata, "tasks": tasks, "server_models": sorted({r["server_model"] for r in records if r.get("server_model")})}


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def comparison_table(summaries: Sequence[Mapping[str, Any]]) -> str:
    headers = ["Run", "Server model", "Task", "OK/total", "WER", "Flags", "Preservation", "Schema valid",
               "Key fact recall", "Evidence dates", "Not accomplished leaks", "Compression", "Wall s", "Tokens/s", "Est. USD"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for summary in summaries:
        for task, stats in sorted(summary["tasks"].items()):
            means = stats["means"]
            row = [summary["label"], ", ".join(summary.get("server_models", [])), task,
                   f"{stats['successes']}/{stats['items']}", means.get("wer", means.get("reference_wer")),
                   means.get("hallucination_flag_count"), means.get("content_preservation_recall"),
                   means.get("schema_valid"), means.get("key_fact_recall"), means.get("evidence_dates_valid"),
                   means.get("not_accomplished_leaks"), means.get("compression_ratio"), means.get("wall_time_s"),
                   means.get("tokens_per_s"), stats["estimated_cost_usd_total"]]
            lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return "\n".join(lines) + "\n"


def read_run(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        records = [json.loads(line) for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        raise BenchmarkError(f"Cannot read summary.json and results.jsonl in {path.name}") from None
    if not isinstance(summary, dict) or not isinstance(summary.get("tasks"), dict) or not isinstance(summary.get("label"), str):
        raise BenchmarkError("Invalid run summary structure")
    for record in records:
        if not isinstance(record, dict) or not all(isinstance(record.get(k), str) for k in ("item_id", "task", "source", "status")):
            raise BenchmarkError("Invalid per-item result structure")
        if record["status"] == "ok" and not isinstance(record.get("output"), str):
            raise BenchmarkError("Successful per-item result has no text output")
    return summary, records


def write_rating_sheets(runs: Sequence[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
                        out_dir: Path, *, rng: random.Random | None = None) -> None:
    randomizer = rng if rng is not None else random.SystemRandom()
    grouped: dict[str, dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]]] = {}
    for summary, records in runs:
        for task in summary.get("requested_tasks", []):
            grouped.setdefault(task, {})
        seen: set[tuple[str, str]] = set()
        for record in records:
            key = (record["task"], record["item_id"])
            if key in seen:
                raise BenchmarkError("Run has duplicate task/item results")
            seen.add(key)
            grouped.setdefault(key[0], {})
            if record["status"] != "ok":
                continue
            grouped[key[0]].setdefault(key[1], []).append((summary, record))
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for task, items in sorted(grouped.items()):
        if task not in {"cleanup", "summarize", "extract", "review", "compile", "stt"}:
            raise BenchmarkError("Unsupported task in rating sheet")
        rows: list[dict[str, Any]] = []
        keys: list[dict[str, Any]] = []
        for item_id, candidates in sorted(items.items()):
            if len({r["source"] for _, r in candidates}) != 1:
                raise BenchmarkError(f"Cannot compare different source texts for item {item_id}")
            # Each row gets an independent alias; ordering cannot reveal a model across items.
            shuffled = list(candidates)
            randomizer.shuffle(shuffled)
            for summary, record in shuffled:
                anonymous_id = uuid.uuid4().hex[:12]
                rows.append({"item_id": item_id, "anonymous_id": anonymous_id, "source": record["source"],
                             "review_candidate": record.get("review_candidate", ""), "output": record["output"],
                             "faithfulness_1_5": "", "fluency_1_5": "", "usefulness_1_5": ""})
                keys.append({"anonymous_id": anonymous_id, "item_id": item_id, "task": task,
                             "label": summary["label"], "run_id": summary.get("run_id"),
                             "server_model": record.get("server_model")})
        assert len(rows) == len(keys)
        csv_path = out_dir / f"ratings-{task}.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=RATING_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        csv_path.chmod(0o600)
        write_json(out_dir / f"ratings-{task}-key.json", keys)


def compare_runs(paths: Sequence[Path], out_dir: Path) -> str:
    resolved = [path.resolve() for path in paths]
    if len(resolved) != len(set(resolved)):
        raise BenchmarkError("Supply each results directory only once")
    runs = [read_run(path) for path in resolved]
    table = comparison_table([summary for summary, _ in runs])
    write_rating_sheets(runs, out_dir)
    _written = (out_dir / "comparison.md").write_text(table, encoding="utf-8")
    return table
