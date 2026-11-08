/**
 * Responses shaped exactly as the real backend produces them
 * (backend/src/msj: batch.queue_status, api.search stream, api.validation_handler),
 * run through the client and its guards.
 */
import { describe, expect, it, vi } from 'vitest';
import { workerStale } from '../state/queue';
import { ApiClient } from './client';
import { ApiError } from './errors';
import type { SearchEvent } from './types';

const SUMMARY = {
  id: '6f1c0f3e9a2b4c7d8e9f0a1b2c3d4e5f',
  entry_date: '2026-11-07',
  created_at: '2026-11-07T21:04:33.120415+00:00',
  title: 'Demo night',
  tags: ['school'],
  source: 'text',
  summary: 'Demoed Kestrel KV to the class.',
  text: 'Demo done. Kestrel KV ran in front of the class.',
  projects: ['Kestrel KV'],
  skills: ['Raft', 'Rust'],
  status: 'done',
  revision: 1,
};

/** batch.queue_status as of the web-api branch: request_pending and last_error, no worker.stale. */
const QUEUE_NOW = {
  batch: {
    running: false,
    started_at: null,
    trigger: null,
    last_finished_at: '2026-11-08T01:02:03.000000+00:00',
    last_result: { processed: 9, failed: 0 },
    last_error: { code: 'BATCH_LEASE_LOST', detail: 'Another worker took the batch lease; the batch stopped.' },
    request_pending: true,
  },
  worker: { last_seen_at: '2026-11-08T10:00:00+00:00', interval_s: 86400.0, next_run_at: '2026-11-09T01:00:00+00:00' },
  counts: { queued: 3, running: 0, failed: 1, blocked: 2 },
  items: [
    {
      entry_id: SUMMARY.id,
      entry_title: 'Demo night',
      entry_date: '2026-11-07',
      job_id: 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
      kind: 'review',
      target: 'cleanup',
      turn: 2,
      status: 'queued',
      blocked_by: [],
      last_error: null,
      not_before: '2026-11-08T10:05:00+00:00',
    },
    {
      entry_id: null,
      entry_title: null,
      entry_date: null,
      job_id: 'c0ffee00c0ffee00c0ffee00c0ffee00',
      kind: 'compile',
      target: null,
      turn: null,
      status: 'failed',
      blocked_by: [],
      last_error: 'Compile route returned HTTP 503 Service Unavailable.',
      not_before: null,
    },
  ],
};

function respond(body: string, init: ResponseInit): ApiClient {
  return new ApiClient({ fetch: () => Promise.resolve(new Response(body, init)), onUnauthorized: vi.fn() });
}

describe('real backend shapes', () => {
  it('reads the queue with request_pending and judges liveness on the server clock', async () => {
    const serverNow = Date.parse('2026-11-08T10:00:20Z');
    const client = respond(JSON.stringify(QUEUE_NOW), {
      status: 200,
      headers: { 'Content-Type': 'application/json', Date: new Date(serverNow).toUTCString() },
    });
    const { snapshot, serverTimeMs } = await client.queueStatus();
    expect(snapshot.batch.request_pending).toBe(true);
    expect(snapshot.batch.last_error?.code).toBe('BATCH_LEASE_LOST');
    expect(snapshot.worker.stale).toBeNull();
    expect(snapshot.items[1]?.entry_id).toBeNull();
    expect(serverTimeMs).toBe(serverNow);
    // Heartbeat 20 s old on the server's clock: stale, even if the browser clock is an hour behind.
    const browserNow = serverNow - 3_600_000;
    expect(workerStale(snapshot, browserNow, serverNow - browserNow)).toBe(true);
    // Five seconds old: alive.
    expect(workerStale(snapshot, serverNow - 15_000, 0)).toBe(false);
  });

  it('prefers the server-computed worker.stale and reads next_batch_at', async () => {
    const decided = { ...QUEUE_NOW, worker: { ...QUEUE_NOW.worker, stale: false, next_batch_at: '2026-11-09T01:00:00+00:00' } };
    const client = respond(JSON.stringify(decided), { status: 200, headers: { 'Content-Type': 'application/json' } });
    const snapshot = await client.queue();
    expect(snapshot.worker.next_batch_at).toBe('2026-11-09T01:00:00+00:00');
    // An old heartbeat does not override the server's judgement.
    expect(workerStale(snapshot, Date.parse('2026-11-08T12:00:00Z'), 0)).toBe(false);
  });

  it('streams search tiers whose results embed the summary item', async () => {
    const lines = [
      { tier: 1, name: 'exact', results: [{ entry_id: SUMMARY.id, score: 1.0, snippet: 'Demo done.', entry: SUMMARY }], elapsed_ms: 0.4 },
      { tier: 2, name: 'substring', results: [], elapsed_ms: 0.6 },
      { tier: 3, name: 'fuzzy', results: [], elapsed_ms: 1.9 },
      { tier: 4, name: 'deterministic', results: [], elapsed_ms: 2.3 },
      {
        tier: 5,
        name: 'semantic',
        results: [],
        elapsed_ms: 2.5,
        unavailable: { code: 'EMBEDDING_MODEL_LOAD_FAILED', detail: 'The configured embedding model could not load.' },
      },
      { done: true, elapsed_ms: 2.6 },
    ];
    const client = respond(lines.map((line) => JSON.stringify(line)).join('\n') + '\n', {
      status: 200,
      headers: { 'Content-Type': 'application/x-ndjson' },
    });
    const events: SearchEvent[] = [];
    await client.search({ q: 'Demo', filters: {}, tiers: [1, 2, 3, 4, 5] }, (event) => events.push(event));
    const first = events[0];
    expect(first?.type === 'tier' ? first.results[0]?.entry?.title : null).toBe('Demo night');
    const semantic = events[4];
    expect(semantic?.type === 'tier' ? semantic.unavailable?.code : null).toBe('EMBEDDING_MODEL_LOAD_FAILED');
  });

  it('passes the coded error of a failed search stream through', async () => {
    const lines = [
      { tier: 1, name: 'exact', results: [], elapsed_ms: 0.4 },
      { done: true, error: { status: 500, code: 'INTERNAL_ERROR', detail: 'Search failed; check storage and service logs.' } },
    ];
    const client = respond(lines.map((line) => JSON.stringify(line)).join('\n'), { status: 200 });
    const events: SearchEvent[] = [];
    await client.search({ q: 'x', filters: {}, tiers: [1] }, (event) => events.push(event));
    const done = events.at(-1);
    expect(done?.type === 'done' ? done.error?.code : null).toBe('INTERNAL_ERROR');
  });

  it('shows each 422 issue as field and failed check', async () => {
    const problem = {
      type: 'about:blank',
      title: 'Validation Error',
      status: 422,
      detail: 'Request parameters are invalid.',
      code: 'VALIDATION_ERROR',
      errors: [
        { field: 'query.limit', type: 'less_than_equal' },
        { field: 'query.offset', type: 'greater_than_equal' },
      ],
    };
    const client = respond(JSON.stringify(problem), { status: 422, headers: { 'Content-Type': 'application/problem+json' } });
    const error = (await client
      .listEntries({ filters: {}, order: 'desc', limit: 500, offset: 0 })
      .catch((caught: unknown) => caught)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe('VALIDATION_ERROR');
    expect(error.details.find((line) => line.label === 'Fields')?.value).toBe(
      'query.limit: less_than_equal\nquery.offset: greater_than_equal',
    );
  });
});
