# my-smart-journal web UI

The browser front end for the journal API v1 (`docs/api-v1-contract.md`): add entries by typing or recording, browse them by date range, search them (tiers 1-5 and relevance ranking), watch and drive the processing queue, export selections as Word-ready plain text, and compile selections into resume material.

Vite + TypeScript (strict) + Preact, hand-written CSS with design tokens, no component library. Every API response is validated at the boundary (`src/api/guards.ts`), so a contract drift shows up as one precise `RESPONSE_INVALID` error naming the field.

## Requirements

- Node 22 or newer, pnpm 12.
- For end-to-end tests: the Playwright Chromium build that matches `@playwright/test` 1.61.1 (`pnpm exec playwright install chromium` if it is missing).

```sh
cd web
pnpm install
```

## Where generated files go

Nothing generated lands in the source tree. Build output, the Vite cache, test results, the Playwright report and screenshots go under the build root:

- `MSJ_WEB_BUILD_ROOT` when set;
- else `/workspace/build/my-smart-journal-web-ui` when `/workspace/build` exists (the development machine);
- else this `web/` directory, where `dist/`, `dist-mock/`, `.vite/`, `test-results/`, `playwright-report/` and `shots/` are gitignored.

## Develop

Against a running backend (proxied from `/api` to `http://127.0.0.1:18765`; override with `MSJ_API_TARGET`):

```sh
MSJ_INSECURE_COOKIES=1 msj serve --host 127.0.0.1 --port 18765   # in the backend environment
pnpm dev                                                         # http://localhost:15173
```

`MSJ_INSECURE_COOKIES=1` lets the session cookie work over plain HTTP on loopback. Sign in with the token from `msj create-token`.

### Mock mode

`pnpm dev:mock` runs the whole UI against an in-browser fake of the API (`src/mock/`), seeded with about 40 fictional entries over five months, two compilations, a failed transcription, an entry that needs attention and a queue to run. Nothing is saved; a reload starts over (the mock session survives reloads in the same tab). Any token signs in except `wrong`.

Switches, added to the page URL:

| Parameter | Effect |
|---|---|
| `mock-signed-in=1` | Start signed in |
| `mock-latency=<ms>` | Delay before each response (default 150) |
| `mock-job-ms=<ms>` | Duration of each simulated job in a batch (default 700) |
| `mock-search-ms=<ms>` | Delay between search tier lines (default 60; tier 5 waits longer) |
| `mock-pickup-ms=<ms>` | Delay before the worker picks up a batch request (default 1500) |
| `mock-worker=stale` | No recent worker heartbeat: the UI shows "worker not running" |
| `mock-batch-error=1` | The last batch failed (`BATCH_FAILED`) |
| `mock-embeddings=disabled` or `model_unavailable` | Semantic search unavailable |
| `mock-token=rotated` | The server token was replaced (`TOKEN_REISSUE_REQUIRED`) |
| `mock-empty=1` | No entries or compilations, to see the empty states |

The mock is loaded only in mock mode; production builds do not contain it.

## Build and deploy

```sh
pnpm build        # type-checks, then bundles to <build root>/dist
```

Point the backend at the bundle; `msj serve` then serves the UI at `/` with a strict CSP:

```sh
export MSJ_WEB_DIR=/workspace/build/my-smart-journal-web-ui/dist   # or wherever the build root is
msj serve --host 127.0.0.1 --port 8765
```

The bundle works under that CSP (`script-src 'self'; style-src 'self'`): there are no inline scripts or styles; the pre-paint theme script is `public/theme-init.js`; fonts are self-hosted. Hashed files live under `assets/` (cached immutably by the server); `index.html`, `theme-init.js`, the manifest and icons sit at the root and are revalidated. The app is installable as a PWA (manifest and icons; no offline mode).

Microphone recording needs a secure context (HTTPS, or `localhost`). Over plain HTTP the Record tab says so and offers a file upload instead.

## Checks

```sh
pnpm typecheck    # app (src) and tooling (configs, e2e, scripts)
pnpm lint         # ESLint with typescript-eslint strict + stylistic, type-aware
pnpm test         # Vitest: API client, guards, NDJSON reader, dates, formatting, the mock API
pnpm e2e          # Playwright, desktop (1440 px) and phone (360 px) projects
pnpm screenshots  # Screenshots of every main view in both themes at both widths -> <build root>/shots
```

`pnpm e2e` builds the mock bundle and serves it with the backend's exact security headers (`scripts/serve-static.mjs`), so a CSP violation fails the run: every test fails on any console error. The suite includes the GUI sweep (`e2e/sweep.spec.ts`): wheeling through the list never moves backward and never scrolls the window, the top bar and table heading stay pinned, opening and closing the queue sheet, popovers and modals keeps the list at full width, no view scrolls sideways at 360 px, and the theme switch shows the mode it switches to, persists, and applies before first paint.

## Layout of the source

```
src/api/        types from the contract, response guards, client, NDJSON reader, error model
src/state/      signals: sign-in, queue polling, facets, selection, view (mirrored in the URL), entry modal
src/pages/      Entries (list, search, relevance) and Compilations
src/dialogs/    entry detail, new/edit entry, export, compile
src/components/ dialog, popover, top bar, queue, markers, icons, recorder, tag input, notices
src/styles/     tokens (two themes), base, layout, list, dialogs
src/mock/       the in-browser API fake and its fictional seed data
e2e/            Playwright specs
```
