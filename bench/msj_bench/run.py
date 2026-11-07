"""CLI orchestration; importing this module never loads credentials or starts jobs."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from dotenv import load_dotenv
import httpx

from .backends import LLMBackend, OpenAIBackend, RunPodLLM, WhisperBackend
from .config import Config, DEFAULT_BUILD, load_config, output_root, validate_url
from .datasets import BENCH_ROOT, JournalItem, load_dataset, load_compile_dataset
from .metrics import compile_metrics, llm_metrics, performance_metrics, stt_metrics
from .report import compare_runs, comparison_table, summarize, write_json, write_rating_sheets
from .runpod_client import BenchmarkError, RunPodClient
from .tasks import TASK_NAMES, messages_for, resource_path, task_schema


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Phase 0 journal STT and LLM benchmarks (commands submit paid jobs unless using a local backend).")
    commands = cli.add_subparsers(dest="command", required=True)
    for command in ("stt", "llm"):
        sub = commands.add_parser(command, help=f"Benchmark {command} on a dataset")
        sub.add_argument("--set", dest="dataset", required=True, help="synthetic, user, or a dataset JSON/directory")
        sub.add_argument("--label", required=True, help="User-supplied model/run label")
        sub.add_argument("--config", type=Path, default=BENCH_ROOT / "config.toml")
        sub.add_argument("--out-dir", type=Path, help="Results root outside the source tree")
        sub.add_argument("--limit", type=int, help="Run only the first N items (smoke/acceptance runs)")
        sub.add_argument("--model", help="Request model ID; server-reported model is recorded separately")
        if command == "llm":
            sub.add_argument("--tasks", default="cleanup,summarize,extract,review", help="Comma-separated cleanup,summarize,extract,review,compile")
            sub.add_argument("--backend", choices=("runpod", "openai"), default="runpod")
            sub.add_argument("--base-url", help="OpenAI-compatible base URL including /v1")
            sub.add_argument("--expect-model", help="Fail items whose server-reported model lacks this substring")
            sub.add_argument("--max-tokens", type=int, help="Positive output token cap for each request")
    report = commands.add_parser("report", help="Compare runs and create blind rating sheets")
    report.add_argument("results_dirs", nargs="+", type=Path)
    report.add_argument("--out-dir", type=Path, default=DEFAULT_BUILD / "results", help="Comparison root outside the source tree")
    return cli


def _runpod(http: httpx.Client, config: Config, endpoint: str) -> RunPodClient:
    key = os.environ.get("RUNPOD_API_KEY", "")
    if not key:
        raise BenchmarkError("Set RUNPOD_API_KEY in the worktree .env or environment before using RunPod")
    return RunPodClient(http, api_key=key, endpoint_id=endpoint, api_base=config.api_base,
                        timeout_s=config.timeout_s, request_timeout_s=config.request_timeout_s,
                        poll_initial_s=config.poll_initial_s, poll_max_s=config.poll_max_s, backoff=config.backoff)


def _new_directory(root: Path, label: str) -> Path:
    if not label.strip() or len(label) > 160:
        raise BenchmarkError("Run label must contain 1-160 characters")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip(".-")
    if not slug:
        raise BenchmarkError("Run label needs at least one letter or digit")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = output_root(root) / f"{timestamp}-{slug}"
    directory.mkdir(parents=True, mode=0o700)
    return directory


def _fingerprint(items: tuple[JournalItem, ...]) -> str:
    data = [{**asdict(item), "audio": str(item.audio) if getattr(item, "audio", None) is not None else None} for item in items]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def benchmark(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    items = load_compile_dataset(args.dataset) if args.command == "llm" and "compile" in args.tasks.split(",") else load_dataset(args.dataset, mode=args.command)
    if args.limit is not None:
        if args.limit <= 0:
            raise BenchmarkError("--limit must be positive")
        items = items[: args.limit]
    assert items, "dataset loading must yield at least one item"
    tasks = ("stt",)
    max_tokens = config.max_tokens
    if args.command == "llm":
        requested = [part.strip() for part in args.tasks.split(",")]
        if not requested or len(requested) != len(set(requested)) or any(t not in TASK_NAMES for t in requested):
            raise BenchmarkError("--tasks must contain unique names from cleanup,summarize,extract,review")
        tasks = tuple(t for t in TASK_NAMES if t in requested)
        max_tokens = args.max_tokens if args.max_tokens is not None else (config.compile_max_tokens if "compile" in requested else config.max_tokens)
        if "compile" in requested and requested != ["compile"]:
            raise BenchmarkError("Compile must be run by itself")
        if max_tokens <= 0:
            raise BenchmarkError("--max-tokens must be positive")
        if "review" in tasks and "cleanup" not in tasks and any(item.reference_cleaned is None for item in items):
            raise BenchmarkError("Review without cleanup requires a reference_cleaned candidate for every item")
    # A missing .env is valid when credentials are supplied through the environment.
    _loaded = load_dotenv(BENCH_ROOT.parent / ".env", override=False)
    backend_name = "runpod" if args.command == "stt" else args.backend
    with httpx.Client(follow_redirects=False) as http:
        llm: LLMBackend | None = None
        whisper: WhisperBackend | None = None
        if args.command == "stt":
            whisper = WhisperBackend(_runpod(http, config, config.stt_endpoint), args.model or config.stt_model)
        elif backend_name == "runpod":
            llm = RunPodLLM(_runpod(http, config, config.llm_endpoint), args.model or config.llm_model)
        else:
            llm = OpenAIBackend(http, base_url=validate_url(args.base_url or config.openai_base_url),
                                model=args.model or config.openai_model, api_key=os.environ.get("OPENAI_API_KEY"),
                                timeout_s=config.openai_timeout_s)
        directory = _new_directory(args.out_dir or config.out_dir, args.label)
        metadata: dict[str, Any] = {
            "format_version": 1, "run_id": directory.name, "label": args.label,
            "created_at": datetime.now(timezone.utc).isoformat(), "backend": backend_name,
            "dataset": str(args.dataset), "dataset_sha256": _fingerprint(items),
            "item_ids": [item.id for item in items], "requested_tasks": list(tasks),
            "requested_model": args.model or (config.stt_model if args.command == "stt" else config.llm_model if backend_name == "runpod" else config.openai_model),
            "endpoint_id": (config.stt_endpoint if args.command == "stt" else config.llm_endpoint) if backend_name == "runpod" else None,
            "max_tokens": max_tokens if args.command == "llm" else None,
            "gpu_hourly_usd": config.hourly_usd if backend_name == "runpod" else None,
            "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
            "prompt_sha256": {task: hashlib.sha256(resource_path("prompts", f"{task}.txt").read_bytes()).hexdigest() for task in tasks if task != "stt"},
            "schema_sha256": {task: hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
                              for task in tasks if (schema := task_schema(task)) is not None},
        }
        write_json(directory / "run.json", metadata)
        print(f"Results: {directory}", flush=True)
        records: list[dict[str, Any]] = []
        results_path = directory / "results.jsonl"
        interrupted = False
        try:
            with results_path.open("x", encoding="utf-8") as handle:
                results_path.chmod(0o600)
                for item in items:
                    cleanup_output: str | None = None
                    for task in tasks:
                        source_text = item.source if task == "compile" else item.raw_transcript
                        record: dict[str, Any] = {"item_id": item.id, "task": task, "source": source_text,
                                                  "reference_cleaned": getattr(item, "reference_cleaned", None), "status": "ok"}
                        try:
                            if task == "stt":
                                assert whisper is not None and item.audio is not None
                                generation = whisper.transcribe(item.audio)
                                metrics = stt_metrics(item.raw_transcript, generation.text)
                            else:
                                assert llm is not None
                                candidate = cleanup_output if "cleanup" in tasks else getattr(item, "reference_cleaned", None)
                                if task == "review":
                                    if candidate is None:
                                        raise BenchmarkError("Review skipped: cleanup failed to produce a candidate")
                                    record["review_candidate"] = candidate
                                    record["review_candidate_origin"] = "cleanup" if "cleanup" in tasks else "reference_cleaned"
                                prompt_source = json.dumps({"period": item.period, "purpose": item.purpose, "entries": list(item.entries)}, ensure_ascii=False) if task == "compile" else item.raw_transcript
                                generation = llm.generate(messages_for(task, prompt_source, candidate=candidate),
                                                          max_tokens=max_tokens, schema=task_schema(task))
                                if args.expect_model and args.expect_model.casefold() not in (generation.model or "").casefold():
                                    raise BenchmarkError(f"Server model {generation.model!r} does not match "
                                                         f"--expect-model {args.expect_model!r} (stale worker?)")
                                if task == "cleanup":
                                    cleanup_output = generation.text
                                metrics = (compile_metrics(generation.text, item, task_schema(task)) if task == "compile" else
                                           llm_metrics(task, item.raw_transcript, generation.text,
                                                       item.reference_cleaned, item.key_facts))
                            record.update(output=generation.text, server_model=generation.model, metrics=metrics,
                                          performance=performance_metrics(generation, config.hourly_usd if backend_name == "runpod" else None))
                        except BenchmarkError as exc:
                            record.update(status="error", error=str(exc))
                            print(f"{item.id}/{task}: {exc}", file=sys.stderr, flush=True)
                        records.append(record)
                        _written = handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                        handle.flush()
        except KeyboardInterrupt:
            interrupted = True
        finally:
            summary = summarize(records, {**metadata, "interrupted": interrupted})
            write_json(directory / "summary.json", summary)
            _written = (directory / "summary.md").write_text(comparison_table([summary]), encoding="utf-8")
            write_rating_sheets([(summary, records)], directory)
        return 130 if interrupted else 1 if any(r["status"] == "error" for r in records) else 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "report":
            directory = _new_directory(args.out_dir, "comparison")
            print(compare_runs(args.results_dirs, directory), end="")
            print(f"Rating sheets and comparison: {directory}")
            return 0
        return benchmark(args)
    except BenchmarkError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Error: {type(exc).__name__} while reading configuration/data or writing results; check paths, UTF-8 encoding, and available space", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; inspect the RunPod dashboard if submission had not returned a job ID", file=sys.stderr)
        return 130
