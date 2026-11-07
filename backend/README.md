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

Inference is selected per task. The RunPod Serverless adapter uses `/run` and status polling, OpenAI passthrough for llama.cpp, and base64 input for faster-whisper. The OpenAI-compatible adapter supports a local llama.cpp server. The fake provider makes tests independent of external networks. Batch runs drain transcription, cleanup, and downstream dependency waves, grouping jobs by provider, endpoint, and model within each wave. The default three-model routing therefore processes each model in one contiguous group. A batch takes a bounded snapshot of existing jobs; work queued during a batch waits for the next pass. Jobs in retry backoff keep their `not_before` deadline even during a forced batch. `failed` jobs exhaust their automatic attempts; transient failures remain `queued` with a visible `last_error` until their retry is due.

Search sends each entry at most once, in the first tier that matches: case-sensitive phrase, case-insensitive substring, fuzzy match, then SQLite FTS5 relevance with a recency boost. Results arrive as NDJSON tier events followed by a completion event. Tier 5 semantic search is a documented extension hook for a later phase. Compilation and the PWA are outside Phase 1. Encryption at rest remains an opt-in future feature.

## API examples

Create text with `POST /api/v1/entries`, JSON `{"text":"I worked on Atlas.","tags":["work"],"entry_date":"2026-11-06"}`. For audio, send multipart field `file` with a supported audio MIME type; optional `title`, `entry_date`, and `tags` (a JSON array string) carry metadata. Both return the created entry and queued processing jobs. Upload file size is bounded by `upload_limit_bytes`, with an additional 16 KiB allowance for request metadata. MIME declarations are checked; decoding failures are reported by transcription jobs.

`GET /api/v1/entries` accepts `limit` and `offset`. `GET /api/v1/entries/{id}` returns current-revision artifacts; `?history=1` includes all revisions. `PATCH /api/v1/entries/{id}` accepts `text`, `title` (nullable), `tags`, and `entry_date`. Editing text queues a fresh pipeline and invalidates unfinished jobs from older revisions. Source recordings and transcripts remain in history. `DELETE /api/v1/entries/{id}` removes database records and records durable audio-file cleanup, retried at startup if filesystem removal fails.

`GET /api/v1/jobs?status=failed` lists terminal failures; omit `status` to include queued retries and running jobs. `POST /api/v1/jobs/{id}/retry` resets a terminal failure's attempt budget. Review findings retain the cleaned artifact and set `needs_attention` on both that artifact and the entry. Invalid structured results fail processing with the schema path in `last_error`; failed jobs never publish an artifact. Model outputs retain the server-reported model and the SHA-256 of the packaged prompt bytes. Missing model metadata remains null unless `expect_model` requires a match, in which case processing fails with a stale-worker error.

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
/workspace/build/my-smart-journal-phase1/.venv/bin/ruff check --cache-dir /workspace/build/my-smart-journal-phase1/ruff-cache .
/workspace/build/my-smart-journal-phase1/.venv/bin/mypy --strict --cache-dir /workspace/build/my-smart-journal-phase1/mypy-cache src
```

Tests use temporary databases, explicit fake routes, and httpx MockTransport; real HTTP transports are blocked in the suite. Serve, worker, and batch processes may share one database. Atomic claims, lease tokens, and revision checks prevent duplicate publication or stale results after an edit. SQLite serializes writes; inference runs outside database transactions. This MVP scans the journal for the first three search tiers and is intended for one user's corpus.

Back up the SQLite database using its backup API or a consistent WAL-aware snapshot together with the audio directory. Data-directory permissions are restricted to the owner. Encryption at rest is deferred behind the `Store` boundary; no encryption is performed in Phase 1. Keep data outside the source checkout so no new gitignore rule is required.
