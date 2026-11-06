# Phase 0 journal benchmarks

A Python 3.14 harness for comparing STT and small LLMs on journal material.
The committed dataset contains 12 clearly fictional entries about software,
embedded engineering, school, and personal activities. There are no recordings
or real personal records in the synthetic dataset. Each JSON entry contains a
raw dictated-style transcript, a cleaned reference, key facts, and a fictional
marker. The raw texts include repetitions, fillers, units, Unicode names,
negations, uncertain outcomes, and explicit self-corrections.

## Setup

Run from `/workspace/worktree/my-smart-journal/phase0`. Dependencies, including
transitive dependencies and test tooling, are pinned in `requirements.txt`.
An editable install reads the worktree's prompts, schema, config, and fixtures.

```sh
export PYTHONDONTWRITEBYTECODE=1
export PIP_CACHE_DIR=/workspace/build/my-smart-journal-phase0/pip-cache
python3 -m venv /workspace/build/my-smart-journal-phase0/.venv
source /workspace/build/my-smart-journal-phase0/.venv/bin/activate
python -m pip install -r bench/requirements.txt
python -m pip install --no-build-isolation --no-deps -e ./bench
python -m msj_bench --help
```

The venv is already created for this worktree. Keep generated outputs outside
source control. The default results root is
`/workspace/build/my-smart-journal-phase0/results`. Override it with `--out-dir`
or `[output].out_dir` in TOML. A source-tree output path is rejected.

## Running benchmarks

The following RunPod commands submit paid jobs. Unit tests and `--help` do not.
Set `RUNPOD_API_KEY` in the worktree's gitignored `.env`, or in the environment.
Only benchmark commands load that exact `.env`; existing environment variables
have precedence. Credentials are not stored in result files, and remote error
bodies and HTTP request headers are never printed. `OPENAI_API_KEY` is optional
for a local server that requires authentication.

```sh
# Smoke first: python -m msj_bench llm --set synthetic --limit 1 --tasks cleanup --label <label>
python -m msj_bench llm --set synthetic \
  --tasks cleanup,summarize,extract,review --backend runpod \
  --label qwen3.5-4b-q4-k-m

python -m msj_bench stt --set user --label whisper-turbo

python -m msj_bench llm --set synthetic --backend openai \
  --base-url http://127.0.0.1:8080/v1 --model local-model \
  --tasks cleanup,summarize,extract,review --label local-cpu
```

Use `--set synthetic`, `--set user`, a JSON file, or a directory containing JSON
entries or paired recordings. See [the private-data layout](data/user/README.md).
JSON accepts one object or an array. Required fields are `id` and
`raw_transcript`; optional fields are `reference_cleaned`, `key_facts` (strings),
and `audio` (a relative file path or HTTP(S) URL). IDs must be unique. Directory
loading checks recording/reference pairs and skips recordings already named in
a JSON manifest. STT requires audio on every item. The text-only synthetic set
cannot serve as an STT benchmark until corresponding recordings are supplied.

`--label` names a run for comparison; it does not swap the endpoint's model.
Swap RunPod models out of band, then supply a fresh label. The requested model
and the server's returned `model` are recorded separately. A missing returned
model is `null`, never inferred from the label. The candidates in
[config.toml](config.toml) are informational and never trigger downloads or
endpoint changes. Candidate file names follow these repository listings:
[350M](https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF/tree/main),
[1.2B](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF/tree/main),
[E2B](https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/tree/main),
[Phi](https://huggingface.co/unsloth/Phi-4-mini-instruct-GGUF/tree/main),
[Qwen](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/tree/main), and
[E4B](https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/tree/main).

Config controls endpoint IDs, RunPod API base, GPU hourly rate (default $0.58),
request/job timeouts, polling backoff, and the output token cap. `--config`
selects a different TOML file; `--model` changes the requested model ID and
`--max-tokens` overrides the LLM cap. Temperature is fixed at zero. RunPod uses
`/run`, then `/status/{id}` with capped exponential polling intervals. Deadline
expiry, interrupted polling, or a polling failure requests `/cancel/{id}`.
Cancellation failures are reported explicitly and require dashboard inspection.
If submission fails before returning a job ID, the harness cannot cancel an
unknown job; inspect the dashboard before retrying. HTTP submissions are never
automatically retried, to avoid duplicate paid jobs. The total polling deadline
starts before submission; individual HTTP phase timeouts also apply.

The faster-whisper request uses `audio` for URLs and `audio_base64` for local
recordings, plus `model = "turbo"`. The RunPod LLM request uses
`openai_route = "/v1/chat/completions"` and `openai_input` with an explicit
`max_tokens` and `extra_body.chat_template_kwargs.enable_thinking = false`.
For direct OpenAI-compatible HTTP, that SDK-style extra body is flattened to
`chat_template_kwargs`. Extract and review request OpenAI-style
`response_format.type = "json_schema"` with a strict JSON schema. Local servers
must support these fields; unsupported requests fail visibly rather than
silently dropping constraints. No streaming is used.

## Tasks and results

Each LLM task has a separate prompt under `prompts/`. Cleanup removes
speech disfluencies without changing facts. Summarize produces a shorter
first-person paragraph. Extract returns projects, skills/technologies,
accomplishments with metrics, people/roles, STAR fragments, and tags according
to [the schema](schemas/extraction.schema.json). Prompts distinguish completed
work from plans and resolve explicit corrections to the final value.

Review takes the raw source plus that run's cleanup output and returns
`{"unsupported_claims": [...]}`. It runs after cleanup regardless of task-list
order. If cleanup failed, review is recorded as an error without a request.
When review is selected without cleanup, each item's cleaned reference is used
as the candidate; it must exist before any requests are made. Results record
the actual candidate and its origin. This makes review a model task whose
output can be inspected and rated. It is not an automatic judge score.

A run creates `<UTC timestamp>-<label>/` containing:

- `run.json`: metadata, requested model, endpoint ID, dataset hash, prompt/schema
  hashes, config hash, task list, token cap, and hourly rate. Dataset hashes cover
  transcript/reference/fact text and audio paths, not audio file contents.
- `results.jsonl`: one record per item/task, flushed after each item/task, with
  source, output, returned model, metrics, timings, token counts, job/worker IDs,
  and finish reason. Review records also include their candidate. Failures are
  separate records with actionable errors; other items/tasks continue.
- `summary.json` and `summary.md`: per-task aggregates and a Markdown table.
- `ratings-<task>.csv` and `ratings-<task>-key.json`: human-rating material with
  identities kept separately. Failed tasks have empty sheets if no output exists.

Exit status is 0 for a completed run, 1 for configuration/request failures, and
130 for interruption. Invalid extract/review JSON is a measured model-quality
failure with diagnostic metrics, not a transport failure. A `length` finish
reason sets `truncated = true`; increase the token cap if needed. Completed
records and a partial summary are retained on interruption. Run directories
are created with mode 0700 and text-result/key files use mode 0600. Files contain
journal text; treat the build results as private data as well.

## Deterministic metrics

STT WER and CER use `jiwer` after Unicode NFKC normalization, case folding,
punctuation removal, and whitespace collapse. Apostrophes are removed and
decimal points between digits are retained, so `3.5` differs from `35` and
`v2` differs from `v3`. Numbers are never expanded to words; `3` and `three`
remain different. CER includes normalized spaces. Lower is better. Empty output
against a nonempty reference scores 1; empty/empty scores 0. For an empty
reference, jiwer reports insertion counts, which may exceed 1.

LLM results contain these diagnostics:

| Metric | Meaning |
| --- | --- |
| `hallucination_flags` / count | Distinct output numbers/date tokens (including English weekdays/months regardless of case), version strings, or capitalized/name-like tokens absent from the source. The individual lists preserve categories. Lower is better. |
| `content_preservation_recall` | Fraction of distinct source content terms retained by cleanup after stopword and filler removal. Higher is better; an empty source content set scores 1. |
| `reference_wer` | Cleanup WER against an optional cleaned reference using the STT normalization. Lower is better. |
| `json_parses`, `schema_valid` | Strict JSON parsing and schema validation for extract/review. Code fences, missing properties, extra properties, and NaN constants fail. |
| `all_strings_grounded`, `ungrounded_fields` | For extract, every nested string field's content terms must occur in the source. Missing terms are listed with field paths. Invalid JSON has unknown grounding (`null`). |
| `key_fact_recall`, `missing_key_facts` | Optional fixture diagnostics: each fact's normalized terms must all appear in the output, regardless of their order. Summaries may intentionally omit facts. |
| `compression_ratio` | Summary word count / source word count after normalization. Lower means shorter, not necessarily better. An empty source yields `null`. |
| `unsupported_claim_count` | Review model's own number of reported unsupported claims. This is not trusted ground truth. |
| `wall_time_s` | Submission/request through completed response, including polling intervals for RunPod. |
| `delayTime`, `executionTime` | RunPod-reported milliseconds; missing fields stay `null`. Cold model loads can be included in execution. |
| `prompt_tokens`, `completion_tokens` | Server-reported counts; missing usage stays `null`. |
| `tokens_per_s` | Completion tokens / RunPod execution seconds, or / HTTP wall seconds for local runs. Includes prefill and other execution work, not a decode-only rate. Unknown usage or zero time yields `null`. |
| `estimated_cost_usd` | `(executionTime + delayTime) / 1000 / 3600 * GPU hourly USD`. Both timings must be present. Local HTTP cost is unknown (`null`). This requested comparison estimate is not an invoice calculation. |

The lexical guard cannot prove faithfulness: it misses changed relationships,
negations, and false claims that reuse source words. Sentence-initial capitals
can produce false flags. A wrong pre-correction number can pass because it
occurs in the source. Grounding requires exact normalized words and can reject
valid paraphrases or inflections. Removing a false-start fact can reduce recall
even when cleanup is correct. Punctuation normalization can also collapse
technology names. Empty cleanup can have zero flags while preserving nothing.
Use the diagnostics together with human ratings and key facts.

Summary means are arithmetic per-item means over successful requests with a
known value; boolean means are pass fractions. `metric_samples` supplies each
denominator. Mean WER is not corpus-weighted WER. Failure counts and total item
counts include transport failures. Total estimated cost covers only successful
records with known timing; failed or cancelled jobs may incur additional cost.
Comparisons should use the same dataset, prompts, token cap, and task list.
Record cold versus warm behavior using distinct labels/repeated runs; the
harness does not submit unrequested warmup calls or parallel jobs.

## Comparing and rating

```sh
python -m msj_bench report \
  /workspace/build/my-smart-journal-phase0/results/<run-a> \
  /workspace/build/my-smart-journal-phase0/results/<run-b>
```

This prints a comparison table and creates a timestamped comparison directory
under the build results root. Use `report --out-dir <root>` to change it. The
report command is fully offline and does not load `.env`.

For each task, successful outputs are grouped by item ID and shuffled across
runs. Every row gets an independent random anonymous ID, with source and output
but no model/run identity columns. The review sheet includes the candidate
being reviewed. The separate key maps anonymous IDs back to run labels, run IDs,
and returned models. Keep the key from raters until scores are final. With only
one compared run, aliases provide a template but cannot conceal the sole run's
identity. Overlapping IDs with different source text are rejected. Runs with
missing/failed items contribute fewer rows; check the table's OK/total counts.

Rate faithfulness, fluency, and usefulness from 1 (poor) to 5 (excellent), leaving
the other columns intact. Faithfulness asks whether facts, uncertainty,
negations, and corrections are preserved. Fluency asks whether the output reads
naturally (or is usable structured JSON). Usefulness asks whether it serves the
requested task. Prefer raters who do not know the model identities and inspect
multiple entries before choosing a candidate. Score aggregation is manual in
phase 0. TODO: optional LLM-as-judge with a separately calibrated rubric; do not
substitute the benchmark's review output for human ground truth.

## Offline verification

```sh
export PYTHONDONTWRITEBYTECODE=1
/workspace/build/my-smart-journal-phase0/.venv/bin/python -m pytest -q -n auto bench/tests
```

Pytest caching is disabled in `pyproject.toml`; test temporary files are directed
to `/workspace/build/my-smart-journal-phase0/pytest-tmp`. Tests use
`httpx.MockTransport` and reject real socket connections. They also replace
`.env` loading, so private credentials are never read by the suite. Coverage
includes mocked CLI runs, request formats, polling/backoff/cancellation, metrics,
dataset validation, and blind-sheet anonymization. Live endpoint validation is
left to the parent session.
