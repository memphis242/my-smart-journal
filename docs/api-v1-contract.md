# API v1 contract: web UI phase

This contract covers the endpoints the web UI needs. The backend and the web UI are built against it in parallel. Existing endpoints that are not listed here keep their current behavior, which is documented in `backend/README.md`. Every path is under `/api/v1`. Errors use RFC 7807 problem JSON (`{type,title,status,detail,code?}`). The `code` field is a stable uppercase identifier that the UI displays next to the message, for example `BATCH_ALREADY_RUNNING`.

## Authentication

- `POST /session` takes `{"token": "<bearer token>"}`. On success it returns 204 and sets the `msj_session` cookie with HttpOnly, SameSite=Strict, Path=/ and a 30-day Max-Age. The cookie is marked Secure unless `MSJ_INSECURE_COOKIES=1` is set, which is for loopback development only. A wrong token returns 401 with code `BAD_TOKEN`. Attempts are rate-limited per client address; when the limit is hit, the response is 429 with code `RATE_LIMITED` and a `Retry-After` header.
- `DELETE /session` clears the cookie and returns 204.
- `GET /session` returns 204 when the request is authenticated and 401 otherwise. The UI uses it to decide whether to show the sign-in dialog.
- Every other endpoint except `/health` accepts either `Authorization: Bearer` or the cookie. Mutating requests (POST, PATCH or DELETE) that authenticate by cookie must also send `X-MSJ-CSRF: 1`; without it the response is 403 with code `CSRF_HEADER_MISSING`.
- The cookie stores an opaque server-side session id, never the token itself. Sessions live in SQLite, and only a hash of each session id is stored. Changing the token with `msj create-token` invalidates every session.

## Entries

`GET /entries` takes these query parameters, all optional:

| Parameter | Meaning |
|---|---|
| `from`, `to` | Inclusive bounds on `entry_date`, written `YYYY-MM-DD` |
| `tag`, `project`, `skill` | Each may repeat. Values are ANDed across kinds and ORed within one kind. Matching is case-insensitive and exact against the facet values (see `GET /facets`) |
| `view` | `summary` (default) or `full`. `full` returns the shape `GET /entries/{id}` returns today |
| `order` | `desc` (default) or `asc`, by `entry_date`, then `created_at`, then `id` |
| `limit` | 1-500, default 100 |
| `offset` | Default 0 |

The response is `{"items": [...], "total": <count matching the filters>}`. This changes the old bare-list shape, which no client depends on. Each item in the `summary` view has this shape:

```json
{
  "id": "…", "entry_date": "2026-11-08", "created_at": "2026-11-08T14:03:11Z",
  "title": "…" | null, "tags": ["…"], "source": "text" | "audio",
  "summary": "…" | null,          // final summary artifact text, if any
  "text": "…",                    // final cleaned text if available, else the raw text
  "projects": ["…"], "skills": ["…"],   // from the final extraction, else []
  "status": "queued" | "processing" | "done" | "needs_attention" | "failed",
  "revision": 1
}
```

`status` is derived from the current revision's jobs and review sessions:

| Status | Condition |
|---|---|
| `failed` | Any job has failed or is blocked by a failure |
| `processing` | Any job is running |
| `queued` | Any job is queued |
| `needs_attention` | The entry's `needs_attention` flag is set |
| `done` | None of the above |

When several conditions hold, the first row in the table wins.

`GET /entries/{id}` is unchanged. Its response also gains the summary-view fields above, so the detail modal can render without picking through artifacts. `POST /entries` is unchanged; the UI uses JSON for text entries and multipart for audio recorded with MediaRecorder (`audio/webm;codecs=opus` or `audio/mp4`).

`GET /entries/{id}/neighbors` takes the same filter parameters as `GET /entries` and returns `{"newer": id|null, "older": id|null}` within that filtered ordering. The detail modal's left and right bars use it at the edges of the loaded page.

## Facets

`GET /facets` returns the known values with entry counts over current revisions, sorted by count (descending), then by value:

```json
{"tags":[{"value":"work","count":12}], "projects":[…], "skills":[…]}
```

Facet values come from the user's tags and from the final extraction's `projects` and `skills_technologies`. They are grouped case-insensitively, and the most frequent spelling is the one shown.

## Search, tiers 1-5

`GET /search?q=…` keeps the existing NDJSON contract and accepts the same filter parameters as `GET /entries`. A `tiers` parameter, a comma list defaulting to `1,2,3,4,5`, allows a cheap search that skips the semantic tier.

Tier 5 is `{"tier":5,"name":"semantic","results":[…],"elapsed_ms":…}`. Each result also carries `"passage": "<best matching chunk>"`. Entries already returned by an earlier tier are excluded, which keeps the existing deduplication contract.

When embeddings are unavailable, tier 5 still emits its event, with `"results": []` and `"unavailable": {"code": "…", "detail": "…"}`. The codes are:

- `EMBEDDINGS_DISABLED`: the embedder is configured as `none`.
- `EMBEDDING_MODEL_UNAVAILABLE`: the model failed to load. The detail says to check `[embedding] model` and the service log.
- `EMBEDDINGS_PENDING`: some entries are not embedded yet. Results are still returned, and the detail gives the count of pending entries.

Embeddings work as follows:

- A local sentence-transformers model runs on the VPS CPU. The default is `BAAI/bge-small-en-v1.5` (384 dimensions), configured under `[embedding]` with `provider = "sentence-transformers" | "fake" | "none"`, `model`, `device = "cpu"`, `chunk_tokens` (default 256) and `chunk_overlap` (default 32).
- The worker embeds the final cleaned text in overlapping chunks, plus the summary as one more chunk, through a new local `embed` job that runs after the cleanup and summary sessions finish.
- The query is embedded with the model's query instruction prefix when it has one.
- An entry's score is its maximum chunk cosine similarity. Results are kept at or above `min_score` (default 0.35) and capped at `top_k` (default 20).
- Vectors are stored as float32 blobs in SQLite and searched brute-force with numpy, which suits a single user's corpus.
- `sentence-transformers` is an optional dependency extra, `msj[semantic]`. Tests use the fake embedder.

`POST /retrieve` is the RAG-style retrieval used to build an export subset. It takes `{"query":"…", "k": 20, "filters": {same filter fields}}` and returns `{"items":[{"entry": <summary item>, "score": 0.71, "passages": ["…"]}], "unavailable": null | {code, detail}}`.

The ranking combines semantic scores with tier 4 BM25 through reciprocal rank fusion. With embeddings unavailable, the ranking uses BM25 alone and `unavailable` is set.

## Queue and batches

`GET /queue` returns:

```json
{
  "batch": {"running": true|false, "started_at": "…"|null, "trigger": "manual"|"schedule"|null,
            "last_finished_at": "…"|null, "last_result": {"processed": 3, "failed": 0}|null},
  "worker": {"last_seen_at": "…"|null, "interval_s": 900, "next_run_at": "…"|null},
  "counts": {"queued": 4, "running": 1, "failed": 0, "blocked": 0},
  "items": [{"entry_id":"…","entry_title":"…"|null,"entry_date":"…","job_id":"…",
             "kind":"cleanup","target":null|"cleanup","turn":null|1,
             "status":"queued"|"running"|"failed","blocked_by":[…],
             "last_error":"…"|null,"not_before":"…"|null}]
}
```

`items` covers the current revisions' non-terminal and failed jobs, oldest first, capped at 200.

The worker writes a heartbeat row on each pass. `next_run_at` is computed from that heartbeat and the interval. When no heartbeat has been seen within twice the interval, `worker.last_seen_at` is stale and the UI shows "worker not running".

`POST /batches/run` now returns 202 with `{"started": true, "batch_id": "…"}`. The batch runs in a background thread in the server process. If a batch is already running in any process, the response is 409 with code `BATCH_ALREADY_RUNNING`. Single-flight is enforced by a lease row in SQLite. `msj run-batch` uses the same lease.

## Export

`POST /export` takes:

```json
{"entry_ids": ["…"],
 "format": "text"|"markdown"|"json",
 "include": {"title": true, "date": true, "tags": true, "summary": true, "text": true,
             "extraction": false},
 "order": "asc"|"desc"}
```

It returns the export as the body, with `Content-Type` `text/plain; charset=utf-8`, `text/markdown` or `application/json`, and an attachment `Content-Disposition` named `journal-export-YYYY-MM-DD.<ext>`.

The `text` format is laid out for pasting into Word:

- Entries are separated by a blank line and a line of dashes.
- The header line is `YYYY-MM-DD  Title`.
- Labeled sections follow the header: `Summary:`, then the text paragraphs, then `Tags:`.
- When `extraction` is included, it is rendered as indented plain lists.
- There are no Markdown symbols.

Unknown ids return 404 with code `ENTRY_NOT_FOUND`, and the detail lists the missing ids, up to 20. More than 1000 ids return 422.

## Compile

`POST /compilations` takes `{"entry_ids":[…], "purpose": "…", "period": "…"}` and returns 202 with the compilation record. This queues a `compile` job, using the compile prompt and the `tasks.compile` route (the example config routes it to Gemma-4-26B-A4B). The job takes part in batches like other jobs, but it is not tied to an entry.

`GET /compilations` and `GET /compilations/{id}` return records shaped like this:

```json
{"id":"…","created_at":"…","status":"queued"|"running"|"done"|"failed","entry_ids":[…],
 "purpose":"…","period":"…","result": <compile JSON>|null,"last_error":"…"|null,
 "model":"…"|null,"prompt_sha256":"…"|null}
```

`DELETE /compilations/{id}` returns 204.

## Static web UI

`msj serve` serves the built UI from `MSJ_WEB_DIR` when that variable is set; otherwise only the API is served. Assets are served with long-lived caching when their file names are hashed. Every unknown non-`/api` path returns `index.html`. The UI responses carry these headers:

- a strict CSP (`default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`);
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: no-referrer`;
- `Permissions-Policy: microphone=(self)`.
