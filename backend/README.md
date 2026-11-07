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

The example routes transcription to a RunPod faster-whisper endpoint, cleanup to Phi-4-mini, and review, summary, and extraction to Gemma-4-26B-A4B (UD-Q4_K_XL, 24 GB GPU). Configure a task with `provider = "openai"` and a local OpenAI-compatible `base_url` to run against a server on your own machine. `provider = "fake"` selects deterministic output for tests and local development. `expect_model` is an optional exact server-reported model guard; use the model string returned by the endpoint. `RUNPOD_API_KEY` is read only from the process environment and must never be stored in TOML or logs.

RunPod audio is sent as base64 in JSON. `runpod_max_payload_bytes` (default `9500000`, environment `MSJ_RUNPOD_MAX_PAYLOAD_BYTES`) caps the complete encoded `/run` body independently of the upload limit. Oversized recordings fail immediately without submission or automatic retry, with guidance to record in Opus/compressed format or split the recording. HTTP 413 from either provider also fails immediately as a non-retryable request-size error. A manual retry remains available after correcting the input or configuration.

`runpod_status_max_failures` (default `3`, environment `MSJ_RUNPOD_STATUS_MAX_FAILURES`) is the number of consecutive transient `/status` failures tolerated before cancellation; `0` cancels on the first failure. A successful status response resets the counter. Transport failures and HTTP 408, 429, and 5xx responses are transient; invalid responses and other HTTP errors fail immediately. A completed job is accepted even when its status arrives after the polling deadline. OpenAI-compatible requests have one total `provider_timeout_s` deadline covering upload, response headers, and response-body reads; expiry cancels the async request before its worker lease can expire.

## Provider errors

Provider HTTP failures appear in the job's `last_error` with the HTTP status, configured endpoint identifier, and bounded provider code or detail when supplied. Authentication errors name the relevant environment variable without exposing its value. For RunPod `ENDPOINT_PAUSED`, set the endpoint's maximum workers to at least 1; if it already shows 1 or more, re-save the workers setting to clear a known scaler state issue. That condition retries within the normal bounded job attempt budget, then remains visible as a failed job. HTTP 401, 403, 404, and 413 errors do not retry automatically; rate limits and provider-side 5xx errors use bounded retries.

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

SQLite uses WAL mode, foreign keys, numbered startup migrations, and a storage boundary that can later support encryption at rest. Audio files are content-addressed by SHA-256. Artifacts are append-only: source revisions and each valid model output remain available with their model and prompt hash. Audio transcription precedes cleanup. Cleanup's review session finishes before summary and extraction start, and each of those outputs has its own review session. A lease allows crashed workers' running jobs to be reclaimed; failed jobs use bounded exponential retries.

Inference is selected per task. The RunPod Serverless adapter uses `/run` and status polling, OpenAI passthrough for llama.cpp, and base64 input for faster-whisper. The OpenAI-compatible adapter supports a local llama.cpp server. The fake provider makes tests independent of external networks. Batch runs drain transcription, cleanup, and review/revision waves, grouping jobs by provider, endpoint, and model within each wave. All ready reviews in a cleanup turn run before that turn's corrections, so model switching happens by wave rather than by entry. A batch snapshots the existing entry/revision pairs and includes bounded session follow-ups created for those pairs. New entries and text revisions wait for another pass. Each job is attempted at most once per batch. Jobs in retry backoff keep their `not_before` deadline even during a forced batch. `failed` jobs have exhausted their attempts or encountered a non-retryable error; transient failures remain `queued` with a visible `last_error` until their retry is due. Unexpected per-job exceptions record a generic actionable error, log only the exception type, and allow the batch to continue processing other entries.

## Review sessions

Review policy is configured independently of the reviewer model route:

```toml
[review]
max_turns = 3
enabled_for = ["cleanup", "summarize", "extract"]
```

`max_turns` accepts 1-3 and defaults to 3. `enabled_for` defaults to all three producers; an empty list disables review. Environment overrides include `MSJ_REVIEW__MAX_TURNS=2` and `MSJ_REVIEW__ENABLED_FOR='["cleanup","extract"]'`. The policy is saved per entry revision so restarting workers or changing configuration does not change an active session's bound.

Cleanup is checked against its original user text or raw transcript. Summary and extraction are checked against the final cleaned artifact from cleanup's completed session. The `tasks.review` route performs every fact check. An empty `unsupported_claims` list accepts the candidate. Otherwise, if another turn is available, a `revise` job returns to the original producer route: Phi for cleanup under the example configuration, Gemma for summary and extraction. The request carries the producer's system prompt, original source payload, previous candidate as an assistant message, and a user revision request containing the reviewer's claims. Extraction revisions retain the original JSON schema constraint.

Each valid correction is appended as a new producer artifact with a `superseded_by` link from the previous version. The next review job is pinned to that exact new artifact. When the last allowed review still reports claims, the latest candidate is retained and marked `needs_attention`, with remaining claims saved in the session. A schema-invalid extraction revision becomes a terminal job failure without publishing its output; correct the model/configuration and explicitly retry it to resume. Failed calls and retries do not consume an additional review turn. Review/revise jobs use the same leases, attempt limits, dependency visibility, and entry-revision fences as other processing jobs.

The producer route is saved with its first valid output, including provider, endpoint, model, expectation, and generation budget. Revisions keep that route even after a worker restart or configuration change; credentials are still read only from the environment. Route URLs cannot embed userinfo, query parameters, or fragments. Changing producer settings for an existing entry requires a new text revision and session. For outputs created before route snapshots existed, migration uses the producer route configured during upgrade. Prompts and the revision template are cached per worker; their hashes use the same cached strings as the message builder and are computed before inference. Restart workers to apply prompt edits.

Entry responses include `review_sessions`, keyed by `cleanup`, `summarize`, and `extract`. Each contains `final_artifact`, `turns_used`, `remaining_claims`, and `status`: `accepted` or `needs_attention` for completed sessions, `pending` for unfinished sessions, or `disabled` when excluded by policy. The `history` array includes each turn's review artifact with `target`, `turn`, `reviewed_artifact_id`, reviewer `model`, and `prompt_sha256`. Claims are journal data and appear only in authenticated artifact/session views, never in logs or job errors. Review artifacts retain separate lineage for each producer; `?history=1` also retains all prior producer versions and entry revisions.

Migration 004 preserves existing artifacts, jobs, and dependency edges while adding session state and review links. Legacy one-pass review records use turn 0, link to their original cleaned artifact when available, and remain in artifact history. Startup initializes review state for existing current-revision work. Stop old serve/worker processes before upgrading and restart them with the new package. Migration invalidates in-flight legacy claims and requeues producer work; a database claim-protocol constraint prevents old worker code from reclaiming jobs after the upgrade. New workers resume processing with fresh leases. Work that already used its attempt budget becomes failed and needs an explicit retry. The session bound also bounds automatic follow-up jobs: three enabled producers require six LLM jobs when every first review accepts, and at most eighteen LLM jobs when each needs all three review turns, plus one transcription job for audio.

Pluggable providers should raise `ProviderError` on failure. Its message is treated as private and replaced with a generic actionable job error; retryability is preserved. Built-in providers and validation code use sanitized error templates for size limits, schema paths, deadlines, and model mismatches. Exception values and model output are never included in diagnostic logs.

Search sends each entry at most once, in the first tier that matches: case-sensitive phrase, case-insensitive substring, fuzzy match, then SQLite FTS5 relevance with a recency boost. Results arrive as NDJSON tier events followed by a completion event. Tier 5 semantic search is a documented extension hook for a later phase. Compilation and the PWA are outside Phase 1. Encryption at rest remains an opt-in future feature.

## API examples

Create text with `POST /api/v1/entries`, JSON `{"text":"I worked on Atlas.","tags":["work"],"entry_date":"2026-11-06"}`. For audio, send multipart field `file` with a supported audio MIME type; optional `title`, `entry_date`, and `tags` (a JSON array string) carry metadata. Both return the created entry and queued processing jobs. Upload file size is bounded by `upload_limit_bytes`, with an additional 16 KiB allowance for request metadata. MIME declarations are checked; decoding failures are reported by transcription jobs.

`GET /api/v1/entries` accepts `limit` and `offset`. `GET /api/v1/entries/{id}` returns current-revision artifacts; `?history=1` includes all revisions. `PATCH /api/v1/entries/{id}` accepts `text`, `title` (nullable), `tags`, and `entry_date`. Valid compact and ISO-week date input is stored canonically as `YYYY-MM-DD`. Editing text queues a fresh pipeline and marks queued, running, and failed jobs from older revisions `superseded`, distinct from current processing failures. Completed jobs, source recordings, and transcripts remain in history. Migration 003 rebuilds the jobs table while preserving dependencies and converts obsolete-revision failures to `superseded`.

`DELETE /api/v1/entries/{id}` commits database deletion and durable audio cleanup requests. Filesystem cleanup errors are logged without journal content and leave the cleanup request pending. One undeletable file does not prevent other files from being removed, turn an already committed deletion into HTTP 500, or prevent startup. Pending cleanup is retried at subsequent startup or deletion passes.

`GET /api/v1/jobs?status=failed` lists terminal failures; omit `status` to include queued retries and running jobs. `POST /api/v1/jobs/{id}/retry` resets a terminal failure's attempt budget. Review and revision jobs include `target` and `turn`, plus artifact IDs pinning their inputs/results. Exhausted review sessions set `needs_attention` on their final artifact and entry. Invalid structured results fail processing with the schema path in `last_error`; failed jobs never publish an artifact. Model outputs retain the server-reported model and the SHA-256 of the packaged prompt bytes. For corrections, `prompt_sha256` hashes the original producer prompt bytes, a newline, and the revision template bytes. Missing model metadata remains null unless `expect_model` requires a match, in which case processing fails with a generic stale-worker error that does not echo the response.

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
