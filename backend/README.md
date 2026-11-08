# my-smart-journal backend

This is a private, single-user FastAPI journal service implementing [API v1](../docs/api-v1-contract.md). It stores entries and immutable processing artifacts in SQLite, queues versioned jobs, and runs cleanup, review, summary, and structured extraction. Text and audio entries share the same downstream pipeline. Search streams results as NDJSON across four deterministic tiers and a local semantic tier. The service also supports browser sessions, retrieval, exports, and asynchronous compilations.

## Setup

Use Python 3.14. Keep the environment and build output outside the source tree:

```sh
export PYTHONDONTWRITEBYTECODE=1
python3.14 -m venv /workspace/build/my-smart-journal-backend-web-api/.venv
/workspace/build/my-smart-journal-backend-web-api/.venv/bin/python -m pip wheel --no-deps --wheel-dir /workspace/build/my-smart-journal-backend-web-api/wheels backend
/workspace/build/my-smart-journal-backend-web-api/.venv/bin/python -m pip install /workspace/build/my-smart-journal-backend-web-api/wheels/my_smart_journal-0.1.0-py3-none-any.whl
/workspace/build/my-smart-journal-backend-web-api/.venv/bin/python -m pip install pytest pytest-xdist pytest-asyncio ruff mypy types-jsonschema
```

Add `/workspace/build/my-smart-journal-backend-web-api/.venv/bin` to `PATH` or use the full path to `msj`. Run the verification commands below from `backend/`. Leave `PYTHONDONTWRITEBYTECODE=1` set to keep bytecode out of the checkout.

## Configuration

Configuration comes from `MSJ_*` environment variables and an optional TOML file selected by `MSJ_CONFIG_FILE`. TOML settings provide task-specific provider routing; explicit environment values override TOML. The full example is in [config.example.toml](config.example.toml). The default data directory is `~/.local/share/my-smart-journal`; set `MSJ_DATA_DIR` to choose another location. The database is `journal.db` and audio is stored under `audio/`.

Without configuration, tasks use an OpenAI-compatible server at `http://127.0.0.1:8080/v1`, with task-specific model names. Configure the actual endpoints before processing real entries. Fake inference requires explicit `provider = "fake"` routing and reports model `fake`; it produces synthetic transcripts and empty extractions for development only.

The example routes transcription to a RunPod faster-whisper endpoint, cleanup to Phi-4-mini, and review, summary, extraction, and compilation to Gemma-4-26B-A4B (UD-Q4_K_XL, 24 GB GPU). Configure a task with `provider = "openai"` and a local OpenAI-compatible `base_url` to run against a server on your own machine. `provider = "fake"` selects deterministic output for tests and local development. `expect_model` is an optional exact server-reported model guard; use the model string returned by the endpoint. `RUNPOD_API_KEY` and `OPENAI_API_KEY` are read only from the process environment and must never be stored in TOML or logs. Authentication guidance names the credential for the configured provider kind, including when an OpenAI-compatible endpoint uses a RunPod hostname.

RunPod audio is sent as base64 in JSON. `runpod_max_payload_bytes` (default `9500000`, environment `MSJ_RUNPOD_MAX_PAYLOAD_BYTES`) caps the complete encoded `/run` body independently of the upload limit. Oversized recordings fail immediately without submission or automatic retry, with guidance to record in Opus/compressed format or split the recording. HTTP 413 from either provider also fails immediately as a non-retryable request-size error. A manual retry remains available after correcting the input or configuration.

`runpod_status_max_failures` (default `3`, environment `MSJ_RUNPOD_STATUS_MAX_FAILURES`) is the number of consecutive transient `/status` failures tolerated before cancellation; `0` cancels on the first failure. A successful status response resets the counter. Transport failures and HTTP 408, 429, and 5xx responses are transient; invalid responses and other HTTP errors fail immediately. A completed job is accepted even when its status arrives after the polling deadline. OpenAI-compatible requests have one total `provider_timeout_s` deadline covering upload, response headers, and response-body reads; expiry cancels the async request before its worker lease can expire.

## Provider errors

Provider HTTP failures appear in the job's `last_error` with the HTTP status, configured endpoint identifier, and a recognized machine code when supplied. Upstream free-form messages, titles, and details are excluded because a provider can echo private journal text or credentials in those fields. Known paused-endpoint, quota, rate-limit, authentication, and model-not-found codes retain specific guidance. Authentication errors name the relevant environment variable without exposing its value. For RunPod `ENDPOINT_PAUSED`, set the endpoint's maximum workers to at least 1; if it already shows 1 or more, re-save the workers setting to clear a known scaler state issue. That condition retries within the normal bounded job attempt budget, then remains visible as a failed job. HTTP 401, 403, 404, and 413 errors do not retry automatically; rate limits and provider-side 5xx errors use bounded retries.

## Run

Create a single-user API token once. The displayed bearer token cannot be recovered later; only its key-identified digest (`hmac-sha256$<kid>$<hex>`) is stored. Only `msj create-token` creates the 256-bit key in `token.secret` in the data directory. The key must be a regular 32-byte file with no group/other permissions (0600 or owner-only 0400 are accepted). Legacy digests, a missing key, or a key-ID mismatch return `TOKEN_REISSUE_REQUIRED`: run `msj create-token`. An unsafe or unreadable key returns actionable `TOKEN_SECRET_INVALID`; logs name the key file. Worker and inline batch processing continue with a warning when token reissue is needed. Back up the secret with the journal database.

```sh
export MSJ_CONFIG_FILE=/path/to/config.toml
export MSJ_DATA_DIR=/path/to/private/journal-data
export RUNPOD_API_KEY=... # only when using RunPod routes
msj migrate
msj create-token
msj serve --host 127.0.0.1 --port 8765
```

The service binds to loopback by default. Send the token as `Authorization: Bearer <token>`. `msj worker` runs a batch immediately and then sleeps for `batch_interval_s` between passes. To process all currently available dependency waves immediately, call `msj run-batch` or `POST /api/v1/batches/run`.

The API is rooted at `/api/v1`; `/api/v1/health` is public. Other API operations accept a bearer token or an opaque browser session cookie. `POST /api/v1/session` exchanges a token for an HttpOnly, SameSite=Strict, 30-day cookie; only a hash of its random session ID is stored in SQLite. Cookies are Secure by default; set `MSJ_INSECURE_COOKIES=1` for loopback HTTP development. Cookie-authenticated POST/PATCH/DELETE requests require `X-MSJ-CSRF: 1`. `GET /api/v1/session` checks authentication and `DELETE /api/v1/session` signs out. Login attempts are limited per client address and return `RATE_LIMITED` with `Retry-After`; invalid tokens return `BAD_TOKEN`. Running `msj create-token` revokes all sessions. Processing errors remain attached to failed jobs and are visible through the jobs API. Retryable failures can be explicitly retried through the API.

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

Pluggable providers should raise `ProviderError` on failure. Its message is treated as private and replaced with a generic actionable job error; retryability is preserved. Built-in providers and validation code use sanitized `SafeProviderError` templates for size limits, schema paths, deadlines, and model mismatches; diagnostics preserve these messages with code `PROVIDER_ERROR`. Private exception values and model output are excluded from diagnostic logs.

Search sends each entry at most once, in the first tier that matches: case-sensitive phrase, case-insensitive substring, fuzzy match, then SQLite FTS5 relevance with a recency boost. Results arrive as NDJSON tier events followed by a completion event. Tier 5 ranks overlapping local embedding chunks by cosine similarity and includes the best matching passage. Encryption at rest is not implemented.

## API examples

Create text with `POST /api/v1/entries`, JSON `{"text":"I worked on Atlas.","tags":["work"],"entry_date":"2026-11-06"}`. For audio, send multipart field `file` with a supported audio MIME type; optional `title`, `entry_date`, and `tags` (a JSON array string) carry metadata. Both return the created entry and queued processing jobs. Upload file size is bounded by `upload_limit_bytes`, with an additional 16 KiB allowance for request metadata. MIME validation compares the base type, accepting `audio/webm;codecs=opus` and `audio/mp4;codecs=mp4a.40.2`; the full declared value is retained in the `raw_audio` artifact's nullable `content_type` field. Decoding failures are reported by transcription jobs.

`GET /api/v1/entries` returns `{items,total}` with `view=summary` by default; `view=full` includes artifacts, jobs, and review sessions. It accepts inclusive `from`/`to` calendar-date bounds, repeated `tag`/`project`/`skill` values (case-insensitive exact matching, OR within a kind and AND across kinds), `order=asc|desc`, `limit` (1-500), and `offset`. Ordering uses entry date, creation time, then ID. Summary and detail views include final summary, cleaned or raw text, projects, skills, and derived status with precedence failed, processing, queued, needs_attention, done. Every entry response includes integer `version`, starting at 1. `GET /api/v1/entries/{id}/neighbors` applies the same filters and returns newer/older IDs. `GET /api/v1/facets` counts current tags, projects, and skills case-insensitively, displaying their most frequent spelling. `GET /api/v1/entries/{id}` returns current-revision artifacts; `?history=1` includes all revisions. `PATCH /api/v1/entries/{id}` accepts `text`, `title` (nullable), `tags`, `entry_date`, and optional strict-integer `expected_version` (booleans are invalid). Every successful PATCH increments `version`; `revision` still increments only for text edits. When a supplied expected version is stale, PATCH returns 409 `ENTRY_CHANGED`, includes `current_version`, and advises reloading and reapplying changes. The version check and update share one SQLite transaction. Successful PATCH responses include the new version. Valid compact and ISO-week date input is stored canonically as `YYYY-MM-DD`. Editing text queues a fresh pipeline and marks queued, running, and failed jobs from older revisions `superseded`, distinct from current processing failures. Completed jobs, source recordings, and transcripts remain in history. Migration 003 rebuilds the jobs table while preserving dependencies and converts obsolete-revision failures to `superseded`; migration 013 adds entry versions, initializing existing entries to 1.

`DELETE /api/v1/entries/{id}` commits database deletion and durable audio cleanup requests. Filesystem cleanup errors are logged without journal content and leave the cleanup request pending. One undeletable file does not prevent other files from being removed, turn an already committed deletion into HTTP 500, or prevent startup. Pending cleanup is retried at subsequent startup or deletion passes.

Deleting an entry also deletes every compilation that references it, including
the compilation's source snapshot, result, and queued or running job. This avoids
retaining a hidden copy of deleted journal content or publishing an in-flight
compilation after its source is removed.

`GET /api/v1/jobs?status=failed` lists terminal failures; omit `status` to include queued retries and running jobs. `POST /api/v1/jobs/{id}/retry` resets a terminal failure's attempt budget. Review and revision jobs include `target` and `turn`, plus artifact IDs pinning their inputs/results. Exhausted review sessions set `needs_attention` on their final artifact and entry. Invalid structured results fail processing with the schema path in `last_error`; failed jobs never publish an artifact. Model outputs retain the server-reported model and the SHA-256 of the packaged prompt bytes. For corrections, `prompt_sha256` hashes the original producer prompt bytes, a newline, and the revision template bytes. Missing model metadata remains null unless `expect_model` requires a match, in which case processing fails with a generic stale-worker error that does not echo the response.

Jobs in the jobs API and entry views include a computed `blocked_by` list identifying terminally failed prerequisites, including failed ancestors, with a message naming the failed job kind. Dependents retain `queued` status while blocked. Retrying the failed prerequisite clears the computed failure block; dependents become runnable once their prerequisites finish successfully. `status=failed` excludes `superseded` work; use `status=superseded` to inspect that terminal state.

`GET /api/v1/search?q=Atlas` streams one NDJSON event per tier:

```json
{"tier":1,"name":"exact","results":[{"entry_id":"...","score":1.0,"snippet":"I worked on Atlas.","entry":{"id":"...","text":"I worked on Atlas.","status":"done"}}],"elapsed_ms":0.1}
```

Tiers 2-5 follow, including empty results, then `{"done":true,"elapsed_ms":1.2}`. Every result includes `entry`, the full summary-view item (abbreviated above), so clients can render without refetching. Each entry appears only in its earliest matching tier. Exact and substring matching use the current text, summary, title, and tags; fuzzy matching uses RapidFuzz partial/token-set ratios. FTS queries quote user tokens and combine them with OR, then rank by weighted BM25 plus a bounded 30-day recency boost. Use `tiers=1,2,3,4` to skip local embedding work, or select any increasing subset of tiers 1-5. Search accepts the same date and facet filters as entries. Tier 5 uses the same entry-ID deduplication contract.

## Semantic search, retrieval, and compilation

Install the optional model dependency with `python -m pip install 'my-smart-journal[semantic]'`
(the distribution is `my-smart-journal`; its command is `msj`).
Numpy is a core dependency. `[embedding]` defaults to provider `sentence-transformers`,
model `BAAI/bge-small-en-v1.5`, device `cpu`, `chunk_tokens=256`,
`chunk_overlap=32`, `min_score=0.35`, and `top_k=20`. Set provider `fake` for
deterministic local development or `none` to disable embeddings. Environment
overrides use names such as `MSJ_EMBEDDING__PROVIDER`.

The local `embed` job waits for cleanup and summary review sessions to finish,
then embeds overlapping cleaned-text chunks and one summary chunk. Normalized
float32 vectors live in SQLite and numpy scans them without a vector server.
Queries use the model's query instruction. Startup reconciliation queues missing
current-revision embeddings, including entries in an upgraded database. Tier 5
returns `EMBEDDINGS_DISABLED`, `EMBEDDING_DEPENDENCY_MISSING`,
`EMBEDDING_MODEL_LOAD_FAILED`, `EMBEDDING_ENCODING_FAILED`, or
`EMBEDDINGS_PENDING` details when applicable; pending entries do not suppress
available results. Model-load failures direct the operator to `[embedding] model`
and the service log without recording private exception values. Model loading
retries with a bounded 30-300 second backoff. Disabling embeddings supersedes
pending local embedding work; re-enabling queues it again. Terminal failures
remain available for explicit retry.

`POST /api/v1/retrieve` accepts `{query,k,filters}` and returns summary entries,
scores, and passages. It combines semantic and BM25 ranks with reciprocal rank
fusion; BM25 remains available when the embedder is unavailable.

`POST /api/v1/batches/run` durably requests work: 202 `{"requested":true}`,
with `"worker":{"stale":true}` when no recent worker heartbeat exists. Repeating
a pending request is idempotent; an active lease yields 409 `BATCH_ALREADY_RUNNING`.
Run `msj worker` separately: it checks requests and the batch schedule every
`worker_tick_s` (2 seconds by default) with interruptible waits. `msj run-batch`
runs inline using the same lease. Local dead owners (host/PID/boot/process start)
are reclaimed. Replaced batch claims are recovered even if individual job leases
have time remaining and retain an actionable reclaim reason in job `last_error`.
Audio-write reservations carry a creation time. Remote-host reservations expire
after `max(3600, 2 * lease_s)` seconds and are logged when skipped; local ones use
process liveness. Worker conflicts log at INFO and
recheck ownership every tick, including after an early lease release. Queue
`next_run_at` is the earlier wall-clock schedule or retry deadline (immediately
due for a new request until the worker observes it); `last_seen_at` reports liveness. Heartbeats during
processing preserve the deadline. `batch.last_error` contains
`{code,detail}` (`BATCH_FAILED` or `BATCH_LEASE_LOST`). Worker heartbeat age above
max(10 seconds, 3 ticks) is stale. The server loads one query embedding model
lazily off the event loop; the worker loads its own shared service.

Compilation jobs have null entry ID, title, and date in queue items because their
source is a selected set of entries rather than one entry.

`POST /api/v1/export` accepts entry IDs, `format=text|markdown|json`, selected
`include` fields, and `order=asc|desc`. The text attachment uses a date/title
header, labeled summary and tags, plain paragraphs, indented extraction lists,
and dashed entry separators for pasting into Word. Missing IDs return 404
`ENTRY_NOT_FOUND` (up to 20 IDs in the detail); exports accept at most 1000 IDs.

`POST /api/v1/compilations` accepts `{entry_ids,purpose,period}`, creates a record,
and queues a batch `compile` job with its own compilation record. It waits for
the selected entries' current processing and review jobs, then snapshots their
finalized content atomically when claimed. Retries keep that source snapshot;
embedding jobs do not block compilation. Failed source jobs remain visible as
prerequisite blocks. It uses the packaged
v3 compile prompt and JSON schema with `tasks.compile` (default model
`Gemma-4-26B-A4B`). `GET /api/v1/compilations` and `GET /api/v1/compilations/{id}`
return status, result, error, model, and prompt hash; DELETE removes a record.
Only schema-valid results are published.

## Web UI and deployment

Set `MSJ_WEB_DIR` to a built UI directory containing `index.html`. Hashed assets
under `assets/` receive immutable caching; extensionless UI routes return the SPA index. Missing chunks, paths with extensions, and dot segments return 404. UI
responses include the contract's strict self-only CSP, nosniff, no-referrer, and
microphone permission policy. Leave it unset to serve only the API. See
[VPS deployment](../docs/deploy-vps.md) and [deployment examples](../deploy/)
for loopback systemd services and a Caddy TLS reverse proxy.

Migration 008 rebuilds the search projection once from completed review outputs. Numbered
migrations preserve existing entries, artifacts, dependency edges, and
review state while adding hashed sessions, entry-independent compilation jobs,
batch leases/heartbeats, and embedding chunks. Stop all old serve/worker processes
before migration and restart them using the upgraded package.

## Verify

From `backend/`, after installing development dependencies:

```sh
export PYTHONDONTWRITEBYTECODE=1
/workspace/build/my-smart-journal-backend-web-api/.venv/bin/python -m pytest -q -n auto -p no:cacheprovider --basetemp=/workspace/build/my-smart-journal-backend-web-api/pytest
/workspace/build/my-smart-journal-backend-web-api/.venv/bin/ruff check --no-cache .
/workspace/build/my-smart-journal-backend-web-api/.venv/bin/mypy --strict --cache-dir /workspace/build/my-smart-journal-backend-web-api/mypy src
```

Tests use temporary databases, explicit fake routes and fake embedders for semantic coverage, and httpx MockTransport; real HTTP transports are blocked in the suite. Serve, worker, and batch processes may share one database. Atomic claims, lease tokens, and revision checks prevent duplicate publication or stale results after an edit. SQLite serializes writes; inference runs outside database transactions. This MVP scans the journal for the first three search tiers and is intended for one user's corpus.

Back up the SQLite database using its backup API or a consistent WAL-aware snapshot together with the audio directory. Data-directory permissions are restricted to the owner. Encryption at rest is deferred behind the `Store` boundary; no encryption is performed. Keep data outside the source checkout so no new gitignore rule is required.

## Upgrade and privacy boundaries

Stop serve/worker before migration 009 and run `msj create-token` after it. Newly
pending cleanup review invalidates downstream published outputs, review sessions,
search projections and vectors; historical artifacts retain their lineage.
Audio creation records a durable cleanup obligation before filesystem writes.
Entry detail includes `compilation_count` for a deletion confirmation.

All problem responses and NDJSON failure events contain a stable code. Internal
ValueErrors yield logged 500 `INTERNAL_ERROR`; logs retain errno/filename, SQLite
codes, validation field/type and traceback file:line evidence. Authored safe errors
also retain their actionable message and stable code; private exception messages,
tokens and journal text remain excluded. API JSON, search and exports use `no-store`.
Queries reject control characters (`INVALID_QUERY`), and retrieval `k` is a strict
integer (no bool, float or string). Retrieval reads one SQLite snapshot.

Login and bearer limiting count failures only, grouping IPv6 clients by /64.
`MSJ_FORWARDED_ALLOW_IPS` defaults to `127.0.0.1,::1`; set the exact trusted proxy
address for a container or remote Caddy. Uvicorn receives this value explicitly.

The embedding revision defaults to
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` and participates in its fingerprint.
After installing the semantic extra, download it with `msj embedding-download`
using the service configuration and `HF_HOME`, before enabling offline units.
Set `HF_HUB_OFFLINE=1` in production (already set in both deployment units).
The units apply `MemoryMax=2G` and systemd privilege restrictions; tune the memory
limit for a deliberately different model. Caddy sets HSTS.
