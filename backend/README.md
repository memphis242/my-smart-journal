# my-smart-journal backend

Phase 1 is a private, single-user FastAPI journal service. It stores entries and immutable processing artifacts in SQLite, queues versioned jobs, and runs cleanup, review, summary, and structured extraction. Text and audio entries share the same downstream pipeline. Search streams results as NDJSON across four deterministic tiers.

## Setup

Use Python 3.14. Keep the environment and build output outside the source tree:

```sh
export PYTHONDONTWRITEBYTECODE=1
python3.14 -m venv /workspace/build/my-smart-journal-phase1/.venv
/workspace/build/my-smart-journal-phase1/.venv/bin/python -m pip wheel --no-deps --wheel-dir /workspace/build/my-smart-journal-phase1/wheels backend
/workspace/build/my-smart-journal-phase1/.venv/bin/python -m pip install /workspace/build/my-smart-journal-phase1/wheels/my_smart_journal-0.1.0-py3-none-any.whl
/workspace/build/my-smart-journal-phase1/.venv/bin/python -m pip install pytest pytest-xdist pytest-asyncio ruff mypy types-jsonschema
```

Add `/workspace/build/my-smart-journal-phase1/.venv/bin` to `PATH` or use the full path to `msj`. Run the verification commands below from `backend/`. Leave `PYTHONDONTWRITEBYTECODE=1` set to keep bytecode out of the checkout.

## Configuration

Configuration comes from `MSJ_*` environment variables and an optional TOML file selected by `MSJ_CONFIG_FILE`. TOML settings provide task-specific provider routing; explicit environment values override TOML. The full example is in [config.example.toml](config.example.toml). The default data directory is `~/.local/share/my-smart-journal`; set `MSJ_DATA_DIR` to choose another location. The database is `journal.db` and audio is stored under `audio/`.

Without configuration, tasks use an OpenAI-compatible server at `http://127.0.0.1:8080/v1`, with task-specific model names. Configure the actual endpoints before processing real entries. Fake inference requires explicit `provider = "fake"` routing and reports model `fake`; it produces synthetic transcripts and empty extractions for development only.

The example routes transcription to a RunPod faster-whisper endpoint, cleanup to Phi-4-mini, and review, summary, and extraction to Gemma-4-E2B. Configure a task with `provider = "openai"` and a local OpenAI-compatible `base_url` to run against a server on your own machine. `provider = "fake"` selects deterministic output for tests and local development. `expect_model` is an optional exact server-reported model guard; use the model string returned by the endpoint. `RUNPOD_API_KEY` is read only from the process environment and must never be stored in TOML or logs.

RunPod audio is sent as base64 in JSON. `runpod_max_payload_bytes` (default `9500000`, environment `MSJ_RUNPOD_MAX_PAYLOAD_BYTES`) caps the complete encoded `/run` body independently of the upload limit. Oversized recordings fail immediately without submission or automatic retry, with guidance to record in Opus/compressed format or split the recording. HTTP 413 from either provider also fails immediately as a non-retryable request-size error. A manual retry remains available after correcting the input or configuration.

`runpod_status_max_failures` (default `3`, environment `MSJ_RUNPOD_STATUS_MAX_FAILURES`) is the number of consecutive transient `/status` failures tolerated before cancellation; `0` cancels on the first failure. A successful status response resets the counter. Transport failures and HTTP 408, 429, and 5xx responses are transient; invalid responses and other HTTP errors fail immediately. A completed job is accepted even when its status arrives after the polling deadline. OpenAI-compatible requests have one total `provider_timeout_s` deadline covering upload, response headers, and response-body reads; expiry cancels the async request before its worker lease can expire.

## Run

Create a single-user API token once. The displayed bearer token cannot be recovered later; only its scrypt hash is stored.

```sh
export MSJ_CONFIG_FILE=/path/to/config.toml
export MSJ_DATA_DIR=/path/to/private/journal-data
export RUNPOD_API_KEY=... # only when using RunPod routes
msj migrate
msj create-token
msj serve --host 127.0.0.1 --port 8765
```

The service binds to loopback by default. Send the token as `Authorization: Bearer <token>`. `msj worker` runs a batch immediately and then sleeps for `batch_interval_s` between passes. To process all currently available dependency waves immediately, call `msj run-batch` or `POST /api/v1/batches/run`.

The API is rooted at `/api/v1`; `/api/v1/health` is public. Other API operations require the bearer token. Processing errors remain attached to failed jobs and are visible through the jobs API. Retryable failures can be explicitly retried through the API.

## Architecture

SQLite uses WAL mode, foreign keys, numbered startup migrations, and a storage boundary that can later support encryption at rest. Audio files are content-addressed by SHA-256. Artifacts are append-only: source revisions and each model output remain available with their model and prompt hash. Jobs form a dependency graph: audio transcription precedes cleanup, and cleanup precedes review, summary, and extraction. A lease allows crashed workers' running jobs to be reclaimed; failed jobs use bounded exponential retries.

Inference is selected per task. The RunPod Serverless adapter uses `/run` and status polling, OpenAI passthrough for llama.cpp, and base64 input for faster-whisper. The OpenAI-compatible adapter supports a local llama.cpp server. The fake provider makes tests independent of external networks. Batch runs drain transcription, cleanup, and downstream dependency waves, grouping jobs by provider, endpoint, and model within each wave. The default three-model routing therefore processes each model in one contiguous group. A batch takes a bounded snapshot of existing jobs; work queued during a batch waits for the next pass. Jobs in retry backoff keep their `not_before` deadline even during a forced batch. `failed` jobs have exhausted their attempts or encountered a non-retryable error; transient failures remain `queued` with a visible `last_error` until their retry is due. Unexpected per-job exceptions record a generic actionable error, log only the exception type, and allow the batch to continue processing other entries.

Search sends each entry at most once, in the first tier that matches: case-sensitive phrase, case-insensitive substring, fuzzy match, then SQLite FTS5 relevance with a recency boost. Results arrive as NDJSON tier events followed by a completion event. Tier 5 semantic search is a documented extension hook for a later phase. Compilation and the PWA are outside Phase 1. Encryption at rest remains an opt-in future feature.

## API examples

Create text with `POST /api/v1/entries`, JSON `{"text":"I worked on Atlas.","tags":["work"],"entry_date":"2026-11-06"}`. For audio, send multipart field `file` with a supported audio MIME type; optional `title`, `entry_date`, and `tags` (a JSON array string) carry metadata. Both return the created entry and queued processing jobs. Upload file size is bounded by `upload_limit_bytes`, with an additional 16 KiB allowance for request metadata. MIME declarations are checked; decoding failures are reported by transcription jobs.

`GET /api/v1/entries` accepts `limit` and `offset`. `GET /api/v1/entries/{id}` returns current-revision artifacts; `?history=1` includes all revisions. `PATCH /api/v1/entries/{id}` accepts `text`, `title` (nullable), `tags`, and `entry_date`. Valid compact and ISO-week date input is stored canonically as `YYYY-MM-DD`. Editing text queues a fresh pipeline and marks queued, running, and failed jobs from older revisions `superseded`, distinct from current processing failures. Completed jobs, source recordings, and transcripts remain in history. Migration 003 rebuilds the jobs table while preserving dependencies and converts obsolete-revision failures to `superseded`.

`DELETE /api/v1/entries/{id}` commits database deletion and durable audio cleanup requests. Filesystem cleanup errors are logged without journal content and leave the cleanup request pending. One undeletable file does not prevent other files from being removed, turn an already committed deletion into HTTP 500, or prevent startup. Pending cleanup is retried at subsequent startup or deletion passes.

`GET /api/v1/jobs?status=failed` lists terminal failures; omit `status` to include queued retries and running jobs. `POST /api/v1/jobs/{id}/retry` resets a terminal failure's attempt budget. Review findings retain the cleaned artifact and set `needs_attention` on both that artifact and the entry. Invalid structured results fail processing with the schema path in `last_error`; failed jobs never publish an artifact. Model outputs retain the server-reported model and the SHA-256 of the packaged prompt bytes. Missing model metadata remains null unless `expect_model` requires a match, in which case processing fails with a stale-worker error.

Jobs in the jobs API and entry views include a computed `blocked_by` list identifying terminally failed prerequisites, including failed ancestors, with a message naming the failed job kind. Dependents retain `queued` status while blocked. Retrying the failed prerequisite clears the computed failure block; dependents become runnable once their prerequisites finish successfully. `status=failed` excludes `superseded` work; use `status=superseded` to inspect that terminal state.

`GET /api/v1/search?q=Atlas` streams one NDJSON event per tier:

```json
{"tier":1,"name":"exact","results":[{"entry_id":"...","score":1.0,"snippet":"I worked on Atlas."}],"elapsed_ms":0.1}
```

Tiers 2-4 follow, including empty results, then `{"done":true,"elapsed_ms":1.2}`. Each entry appears only in its earliest matching tier. Exact and substring matching use the current text, summary, title, and tags; fuzzy matching uses RapidFuzz partial/token-set ratios. FTS queries quote user tokens and combine them with OR, then rank by weighted BM25 plus a bounded 30-day recency boost. Tier 5 TODO: add an embedding-backed provider after tier 4, keeping the same entry-ID deduplication contract.

## Verify

From `backend/`, after installing development dependencies:

```sh
export PYTHONDONTWRITEBYTECODE=1
/workspace/build/my-smart-journal-phase1/.venv/bin/python -m pytest -q -n auto -p no:cacheprovider --basetemp=/workspace/build/my-smart-journal-phase1/pytest
/workspace/build/my-smart-journal-phase1/.venv/bin/ruff check --no-cache .
/workspace/build/my-smart-journal-phase1/.venv/bin/mypy --strict --cache-dir /workspace/build/my-smart-journal-phase1/mypy src
```

Tests use temporary databases, explicit fake routes, and httpx MockTransport; real HTTP transports are blocked in the suite. Serve, worker, and batch processes may share one database. Atomic claims, lease tokens, and revision checks prevent duplicate publication or stale results after an edit. SQLite serializes writes; inference runs outside database transactions. This MVP scans the journal for the first three search tiers and is intended for one user's corpus.

Back up the SQLite database using its backup API or a consistent WAL-aware snapshot together with the audio directory. Data-directory permissions are restricted to the owner. Encryption at rest is deferred behind the `Store` boundary; no encryption is performed in Phase 1. Keep data outside the source checkout so no new gitignore rule is required.
