import { describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import { ApiError } from '../api/errors';
import type { SeedConcept, SeedEntry } from './seed-types';
import { createMockApi } from './server';

const NOW = Date.parse('2026-11-08T18:00:00.000Z');
const CONCEPTS: readonly SeedConcept[] = [];

function seed(overrides: Partial<SeedEntry> = {}): SeedEntry {
  return {
    days_ago: 40,
    time: '09:10',
    source: 'text',
    title: 'Entry for contract tests',
    tags: ['work'],
    raw: 'I improved the Atlas service with TypeScript.',
    cleaned: 'I improved the Atlas service with TypeScript.',
    summary: 'I improved the Atlas service.',
    projects: ['Atlas'],
    skills: ['TypeScript'],
    accomplishments: [{ description: 'Improved the service', metrics: ['18 ms'] }],
    people_roles: [{ name: 'Morgan', role: 'reviewer' }],
    star_fragments: [{ situation: 'The endpoint was slow', task: 'Reduce latency', action: 'Removed excess work', result: '18 ms response time' }],
    ...overrides,
  };
}

function setup(options: {
  readonly seeds?: readonly SeedEntry[];
  readonly now?: () => number;
  readonly jobDelayMs?: number;
  readonly pickupDelayMs?: number;
  readonly batchError?: boolean;
  readonly workerStale?: boolean;
  readonly tokenRotated?: boolean;
  readonly signedIn?: boolean;
  readonly signOutFails?: boolean;
} = {}) {
  const mock = createMockApi({
    seeds: options.seeds ?? [],
    compilations: [],
    concepts: CONCEPTS,
    now: options.now ?? (() => NOW),
    ...(options.jobDelayMs === undefined ? {} : { jobDelayMs: options.jobDelayMs }),
    ...(options.pickupDelayMs === undefined ? {} : { pickupDelayMs: options.pickupDelayMs }),
    ...(options.batchError === undefined ? {} : { batchError: options.batchError }),
    ...(options.workerStale === undefined ? {} : { workerStale: options.workerStale }),
    ...(options.tokenRotated === undefined ? {} : { tokenRotated: options.tokenRotated }),
    ...(options.signedIn === undefined ? {} : { signedIn: options.signedIn }),
    ...(options.signOutFails === undefined ? {} : { signOutFails: options.signOutFails }),
  });
  const client = new ApiClient({ fetch: mock.fetch, onUnauthorized: () => undefined });
  return { mock, client };
}

async function signIn(client: ApiClient): Promise<void> {
  await client.signIn('replacement-token');
}

describe('updated session and batch contract', () => {
  it('returns sessionProblem and requires a replacement token after rotation', async () => {
    const { client } = setup({ signedIn: true, tokenRotated: true });
    await expect(client.sessionProblem()).resolves.toMatchObject({ code: 'TOKEN_REISSUE_REQUIRED', status: 401 });
    await expect(client.facets()).rejects.toMatchObject({ code: 'TOKEN_REISSUE_REQUIRED', status: 401 });
    await signIn(client);
    await expect(client.sessionProblem()).resolves.toBeNull();
    await expect(client.facets()).resolves.toMatchObject({ tags: [], projects: [], skills: [] });
  });

  it('returns stable problem codes for auth, missing resources, conflicts, and retry limits', async () => {
    const { mock, client } = setup({ signedIn: true, pickupDelayMs: 100 });
    await expect(client.signIn('wrong')).rejects.toMatchObject({ code: 'BAD_TOKEN', status: 401 });
    const csrf = await mock.fetch('/api/v1/entries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'No CSRF' }),
    });
    await expect(csrf.json()).resolves.toMatchObject({ code: 'CSRF_HEADER_MISSING', status: 403 });
    await expect(client.getEntry('missing-entry')).rejects.toMatchObject({ code: 'ENTRY_NOT_FOUND', status: 404 });
    await expect(client.retryJob('missing-job')).rejects.toMatchObject({ code: 'JOB_NOT_FOUND', status: 404 });
    await expect(client.getCompilation('missing-compilation')).rejects.toMatchObject({ code: 'COMPILATION_NOT_FOUND', status: 404 });
    await expect(client.createCompilation({ entry_ids: ['missing-entry'], purpose: 'portfolio', period: '2026' }))
      .rejects.toMatchObject({ code: 'ENTRY_NOT_FOUND', status: 404 });
    await client.runBatch();
    await mock.idle();

    const limited = setup({ now: () => NOW });
    for (let attempt = 0; attempt < 5; attempt += 1) {
      await expect(limited.client.signIn('wrong')).rejects.toMatchObject({ code: 'BAD_TOKEN' });
    }
    await expect(limited.client.signIn('wrong')).rejects.toMatchObject({ code: 'RATE_LIMITED', status: 429, retryAfterS: 60 });

    const concurrent = setup({ now: () => NOW });
    const attempts = await Promise.allSettled(Array.from({ length: 6 }, () => concurrent.client.signIn('wrong')));
    const codes = attempts.map((attempt) => attempt.status === 'rejected' && attempt.reason instanceof ApiError
      ? attempt.reason.code
      : 'UNEXPECTED_SUCCESS');
    expect(codes.filter((code) => code === 'BAD_TOKEN')).toHaveLength(5);
    expect(codes.filter((code) => code === 'RATE_LIMITED')).toHaveLength(1);
  });

  it('rejects malformed control queries with actionable validation responses', async () => {
    const { mock } = setup({ signedIn: true });
    for (const path of [
      '/api/v1/entries?from=2026-02-30',
      '/api/v1/entries?to=not-a-date',
      '/api/v1/entries?order=down',
      '/api/v1/search?q=atlas&tiers=1,,2',
      '/api/v1/search?q=',
    ]) {
      const response = await mock.fetch(path, { method: 'GET' });
      expect(response.status, path).toBe(422);
      const problem: unknown = await response.json();
      expect(problem).toMatchObject({ type: 'about:blank', status: 422 });
    }
    const control = await mock.fetch('/api/v1/search?q=%00', { method: 'GET' });
    await expect(control.json()).resolves.toMatchObject({ code: 'INVALID_QUERY', status: 422 });
    const retrievalControl = await mock.fetch('/api/v1/retrieve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-MSJ-CSRF': '1' },
      body: JSON.stringify({ query: 'atlas\u0001' }),
    });
    await expect(retrievalControl.json()).resolves.toMatchObject({ code: 'INVALID_QUERY', status: 422 });
  });

  it('keeps problem codes stable across route, request, and resource failures', async () => {
    const { mock, client } = setup({ signedIn: true });
    const entry = await client.createTextEntry({
      text: 'A queued entry for coded errors.', title: 'Coded error fixture', tags: [], entry_date: '2026-11-08',
    });
    const queuedJob = entry.jobs[0];
    expect(queuedJob).toBeDefined();
    if (queuedJob === undefined) throw new Error('created entry has queued jobs');
    const cases = [
      {
        name: 'unknown route',
        code: 'NOT_FOUND',
        status: 404,
        request: (): Promise<Response> => mock.fetch('/api/v1/no-route', { method: 'GET' }),
      },
      {
        name: 'missing entry',
        code: 'ENTRY_NOT_FOUND',
        status: 404,
        request: (): Promise<Response> => mock.fetch('/api/v1/entries/missing-entry', { method: 'GET' }),
      },
      {
        name: 'missing retry job',
        code: 'JOB_NOT_FOUND',
        status: 404,
        request: (): Promise<Response> => mock.fetch('/api/v1/jobs/missing-job/retry', {
          method: 'POST', headers: { 'X-MSJ-CSRF': '1' },
        }),
      },
      {
        name: 'missing compilation',
        code: 'COMPILATION_NOT_FOUND',
        status: 404,
        request: (): Promise<Response> => mock.fetch('/api/v1/compilations/missing-compilation', { method: 'GET' }),
      },
      {
        name: 'invalid search query',
        code: 'INVALID_QUERY',
        status: 422,
        request: (): Promise<Response> => mock.fetch('/api/v1/search?q=%00', { method: 'GET' }),
      },
      {
        name: 'invalid list bounds',
        code: 'VALIDATION_ERROR',
        status: 422,
        request: (): Promise<Response> => mock.fetch('/api/v1/entries?limit=0', { method: 'GET' }),
      },
      {
        name: 'wrong method',
        code: 'METHOD_NOT_ALLOWED',
        status: 405,
        request: (): Promise<Response> => mock.fetch('/api/v1/health', {
          method: 'POST', headers: { 'X-MSJ-CSRF': '1' },
        }),
      },
      {
        name: 'malformed patch JSON',
        code: 'BAD_REQUEST',
        status: 400,
        request: (): Promise<Response> => mock.fetch(`/api/v1/entries/${entry.id}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-MSJ-CSRF': '1' }, body: '{',
        }),
      },
      {
        name: 'unsupported entry content type',
        code: 'UNSUPPORTED_MEDIA_TYPE',
        status: 415,
        request: (): Promise<Response> => mock.fetch('/api/v1/entries', {
          method: 'POST', headers: { 'Content-Type': 'application/xml', 'X-MSJ-CSRF': '1' }, body: '<entry />',
        }),
      },
      {
        name: 'retry of a queued job',
        code: 'CONFLICT',
        status: 409,
        request: (): Promise<Response> => mock.fetch(`/api/v1/jobs/${queuedJob.id}/retry`, {
          method: 'POST', headers: { 'X-MSJ-CSRF': '1' },
        }),
      },
    ] as const;
    for (const testCase of cases) {
      const response = await testCase.request();
      expect(response.status, testCase.name).toBe(testCase.status);
      expect(response.headers.get('Content-Type'), testCase.name).toContain('application/problem+json');
      const problem: unknown = await response.json();
      expect(problem, testCase.name).toMatchObject({ type: 'about:blank', status: testCase.status, code: testCase.code });
      if (typeof problem !== 'object' || problem === null || Array.isArray(problem)) {
        throw new Error(`${testCase.name} did not return a problem object`);
      }
      const code = (problem as Readonly<Record<string, unknown>>)['code'];
      expect(typeof code, testCase.name).toBe('string');
      expect(code, testCase.name).toMatch(/^[A-Z][A-Z0-9_]*$/);
    }
  });

  it('returns the 202 request shape and accepts another batch after the worker drains it', async () => {
    const { mock, client } = setup({ signedIn: true });
    const response = await mock.fetch('/api/v1/batches/run', {
      method: 'POST', headers: { 'X-MSJ-CSRF': '1' },
    });
    expect(response.status).toBe(202);
    const body: unknown = await response.json();
    expect(body).toEqual({ requested: true });
    await mock.idle();
    await expect(client.queue()).resolves.toMatchObject({ batch: { running: false } });
    await expect(client.runBatch()).resolves.toMatchObject({ requested: true });
    await mock.idle();
  });

  it('distinguishes pending pickup, active processing, and a stale worker request', async () => {
    vi.useFakeTimers();
    try {
      const delayed = setup({ pickupDelayMs: 30, jobDelayMs: 50, signedIn: true });
      const entry = await delayed.client.createTextEntry({
        text: 'Queued work for pickup.',
        title: 'Pickup test',
        tags: [],
        entry_date: '2026-11-08',
      });
      await expect(delayed.client.runBatch()).resolves.toEqual({ requested: true, worker: null });
      await expect(delayed.client.queue()).resolves.toMatchObject({ batch: { running: false, request_pending: true } });
      // A second request while one is pending is idempotent.
      await expect(delayed.client.runBatch()).resolves.toEqual({ requested: true, worker: null });
      await vi.advanceTimersByTimeAsync(29);
      await expect(delayed.client.queue()).resolves.toMatchObject({ batch: { running: false, request_pending: true } });
      await vi.advanceTimersByTimeAsync(2);
      await expect(delayed.client.queue()).resolves.toMatchObject({ batch: { running: true, trigger: 'manual', request_pending: false } });
      await expect(delayed.client.runBatch()).rejects.toMatchObject({ code: 'BATCH_ALREADY_RUNNING', status: 409 });
      await vi.runAllTimersAsync();
      await delayed.mock.idle();
      await expect(delayed.client.queue()).resolves.toMatchObject({ batch: { running: false } });
      await expect(delayed.client.getEntry(entry.id)).resolves.toMatchObject({ status: 'done' });

      const stale = setup({ workerStale: true, pickupDelayMs: 10, signedIn: true });
      await expect(stale.client.runBatch()).resolves.toEqual({ requested: true, worker: { stale: true } });
      await vi.advanceTimersByTimeAsync(1000);
      await stale.mock.idle();
      // Nobody picks the request up while the worker is stopped; it stays pending.
      await expect(stale.client.queue()).resolves.toMatchObject({
        batch: { running: false, trigger: null, request_pending: true },
        worker: { stale: true },
      });
      await expect(stale.client.runBatch()).resolves.toEqual({ requested: true, worker: { stale: true } });
    } finally {
      vi.useRealTimers();
    }
  });

  it('records batch failures and clears the batch error after a successful run', async () => {
    const { client, mock } = setup({ batchError: true, signedIn: true });
    const failed = await client.queue();
    expect(failed.batch.last_result).toEqual({ processed: 5, failed: 0 });
    expect(failed.batch.last_error).toEqual({
      code: 'BATCH_FAILED',
      detail: 'The batch stopped after an unexpected error in the extract wave; 4 jobs were left queued.',
    });
    await client.runBatch();
    await mock.idle();
    const succeeded = await client.queue();
    expect(succeeded.batch.last_result?.failed).toBe(0);
    expect(succeeded.batch.last_error).toBeNull();
  });

  it('counts cited compilations and deletes queued or running compilations with their entry', async () => {
    vi.useFakeTimers();
    try {
      const { client, mock } = setup({ seeds: [seed({ days_ago: 40 })], jobDelayMs: 15, signedIn: true });
      const entries = await client.listEntries({ filters: {}, order: 'desc', limit: 10, offset: 0 });
      const entry = entries.items[0];
      expect(entry).toBeDefined();
      if (entry === undefined) throw new Error('compilation fixture exists');
      await client.runBatch();
      await vi.advanceTimersByTimeAsync(1000);
      await mock.idle();
      const queued = await client.createCompilation({ entry_ids: [entry.id], purpose: 'portfolio', period: '2026' });
      const running = await client.createCompilation({ entry_ids: [entry.id], purpose: 'review', period: '2026' });
      await expect(client.getEntry(entry.id)).resolves.toMatchObject({ compilation_count: 2 });
      await client.runBatch();
      await vi.advanceTimersByTimeAsync(2);
      await expect(client.getCompilation(queued.id)).resolves.toMatchObject({ status: 'running' });
      await expect(client.getCompilation(running.id)).resolves.toMatchObject({ status: 'queued' });
      await client.deleteEntry(entry.id);
      await vi.advanceTimersByTimeAsync(1000);
      await mock.idle();
      await expect(client.getCompilation(queued.id)).rejects.toMatchObject({ code: 'COMPILATION_NOT_FOUND', status: 404 });
      await expect(client.getCompilation(running.id)).rejects.toMatchObject({ code: 'COMPILATION_NOT_FOUND', status: 404 });
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('entry versions and sign-out failures', () => {
  it('increments the version on every PATCH and refuses a stale expected_version', async () => {
    const { client } = setup({ signedIn: true });
    const created = await client.createTextEntry({ text: 'Versioned note.', title: 'v', tags: [], entry_date: '2026-11-08' });
    expect(created.version).toBe(1);
    const retitled = await client.patchEntry(created.id, { title: 'v2', expected_version: 1 });
    expect(retitled.version).toBe(2);
    const unconditional = await client.patchEntry(created.id, { tags: ['work'] });
    expect(unconditional.version).toBe(3);
    const refused = await client.patchEntry(created.id, { title: 'late', expected_version: 2 }).catch((caught: unknown) => caught);
    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).code).toBe('ENTRY_CHANGED');
    expect((refused as ApiError).status).toBe(409);
    expect((refused as ApiError).problem?.['current_version']).toBe(3);
    await expect(client.getEntry(created.id)).resolves.toMatchObject({ title: 'v2', version: 3 });
  });

  it('can fail sign-out with a coded server error', async () => {
    const { client } = setup({ signedIn: true, signOutFails: true });
    await expect(client.signOut()).rejects.toMatchObject({ code: 'INTERNAL_ERROR', status: 500 });
    await expect(client.sessionProblem()).resolves.toBeNull();
  });
});
