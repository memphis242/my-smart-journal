/**
 * Checks the running stack, seeds enough dated, tagged entries for scrolling
 * and filtering, generates a small Opus recording, and drains the queue.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { request, type APIRequestContext } from '@playwright/test';
import { buildRoot } from '../scripts/build-root.ts';

const SEED_TAG = 'e2e-seed';
const SEED_COUNT = 36;
const TOPICS = [
  ['work', 'Reviewed the retry queue and wrote down why the backoff doubles each attempt.'],
  ['work', 'Paired on the PostgreSQL index migration; the unique index needs a quiet window.'],
  ['school', 'Read the Raft paper section on log matching and drew the term diagram twice.'],
  ['life', 'Long run along the river, easy pace, legs heavy after the hills on Sunday.'],
  ['work', 'On-call handover: one noisy alert left, threshold change still not merged.'],
  ['school', 'Distributed systems lab: the leader election test flaked twice out of twenty.'],
] as const;

function readToken(): string {
  const direct = process.env.MSJ_E2E_TOKEN;
  if (direct !== undefined && direct.trim() !== '') return direct.trim();
  const file = process.env.MSJ_E2E_TOKEN_FILE;
  if (file === undefined || file === '') {
    throw new Error('Set MSJ_E2E_TOKEN or MSJ_E2E_TOKEN_FILE to the token printed by `msj create-token` (see README "Run against a local backend").');
  }
  return readFileSync(file, 'utf8').trim();
}

function isoDaysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

async function json<T>(api: APIRequestContext, method: 'get' | 'post', url: string, data?: unknown): Promise<T> {
  const response = await api[method](url, data === undefined ? {} : { data });
  if (!response.ok()) throw new Error(`${method.toUpperCase()} ${url} failed: HTTP ${response.status()} ${await response.text()}`);
  return (await response.json()) as T;
}

async function drainQueue(api: APIRequestContext): Promise<void> {
  const deadline = Date.now() + 60_000;
  for (;;) {
    const queue = await json<{ batch: { running: boolean; request_pending: boolean }; counts: { queued: number; running: number } }>(api, 'get', '/api/v1/queue');
    if (!queue.batch.running && !queue.batch.request_pending && queue.counts.queued === 0 && queue.counts.running === 0) return;
    if (!queue.batch.running && !queue.batch.request_pending) await json(api, 'post', '/api/v1/batches/run');
    if (Date.now() > deadline) throw new Error('The queue did not drain within 60 s; is `msj worker` running against the same data directory?');
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

export default async function globalSetup(): Promise<void> {
  const baseURL = process.env.MSJ_E2E_BASE_URL ?? 'http://127.0.0.1:18765';
  const token = readToken();
  process.env.MSJ_E2E_TOKEN = token;
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Authorization: `Bearer ${token}` } });
  try {
    const health = await api.get('/api/v1/health').catch((error: unknown) => {
      throw new Error(`The backend at ${baseURL} is not reachable (${String(error)}). Start it as described in README "Run against a local backend".`, {
        cause: error,
      });
    });
    if (!health.ok()) throw new Error(`GET ${baseURL}/api/v1/health answered HTTP ${health.status()}`);
    const page = await api.get('/');
    if (!(await page.text()).includes('id="app"')) throw new Error(`${baseURL}/ does not serve the web UI; set MSJ_WEB_DIR to the production build.`);

    const seeded = await json<{ total: number }>(api, 'get', `/api/v1/entries?tag=${SEED_TAG}&limit=1`);
    for (let index = seeded.total; index < SEED_COUNT; index += 1) {
      const [tag, text] = TOPICS[index % TOPICS.length] ?? TOPICS[0];
      await json(api, 'post', '/api/v1/entries', {
        title: `Seed ${String(index + 1).padStart(2, '0')}`,
        text: `${text} (seeded note ${index + 1})`,
        tags: [SEED_TAG, tag],
        entry_date: isoDaysAgo(index * 3),
      });
    }
    await drainQueue(api);
  } finally {
    await api.dispose();
  }

  // A one-second Opus recording for the file-chooser upload path.
  const audio = path.join(buildRoot(), 'e2e-real-assets', 'tone.webm');
  if (!existsSync(audio)) {
    mkdirSync(path.dirname(audio), { recursive: true });
    try {
      execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:a', 'libopus', '-b:a', '24k', audio]);
    } catch (error) {
      throw new Error(`ffmpeg could not create ${audio} (${String(error)}). Install ffmpeg with libopus.`, { cause: error });
    }
  }
  process.env.MSJ_E2E_AUDIO = audio;
}
