import { describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import { entryDetail } from '../api/guards';
import type { SeedCompilation, SeedConcept, SeedEntry } from './seed-types';
import { createMockApi } from './server';

const FIXED_NOW = Date.parse('2026-11-08T18:00:00.000Z');
const NO_CONCEPTS: readonly SeedConcept[] = [];

function fixture(overrides: Partial<SeedEntry> = {}): SeedEntry {
  return {
    days_ago: 40,
    time: '10:15',
    source: 'text',
    title: 'Build notes',
    tags: ['work'],
    raw: 'I improved the Atlas service with TypeScript. The endpoint returns in 18 ms.',
    cleaned: 'I improved the Atlas service with TypeScript. The endpoint returns in 18 ms.',
    summary: 'I improved the Atlas service with TypeScript.',
    projects: ['Atlas'],
    skills: ['TypeScript'],
    accomplishments: [{ description: 'Reduced response time', metrics: ['18 ms'] }],
    people_roles: [{ name: 'Lee', role: 'reviewer' }],
    star_fragments: [{ situation: 'A slow endpoint', task: 'Improve the endpoint', action: 'Removed extra work', result: '18 ms response time' }],
    ...overrides,
  };
}

function setup(options: {
  readonly seeds?: readonly SeedEntry[];
  readonly compilations?: readonly SeedCompilation[];
  readonly now?: () => number;
  readonly latencyMs?: number;
  readonly jobDelayMs?: number;
  readonly pickupDelayMs?: number;
  readonly searchDelayMs?: number;
  readonly workerStale?: boolean;
  readonly signedIn?: boolean;
  readonly session?: { get(): boolean; set(value: boolean): void };
} = {}) {
  const mock = createMockApi({
    seeds: options.seeds ?? [],
    compilations: options.compilations ?? [],
    concepts: NO_CONCEPTS,
    now: options.now ?? (() => FIXED_NOW),
    ...(options.latencyMs === undefined ? {} : { latencyMs: options.latencyMs }),
    ...(options.jobDelayMs === undefined ? {} : { jobDelayMs: options.jobDelayMs }),
    ...(options.pickupDelayMs === undefined ? {} : { pickupDelayMs: options.pickupDelayMs }),
    ...(options.searchDelayMs === undefined ? {} : { searchDelayMs: options.searchDelayMs }),
    ...(options.workerStale === undefined ? {} : { workerStale: options.workerStale }),
    ...(options.signedIn === undefined ? {} : { signedIn: options.signedIn }),
    ...(options.session === undefined ? {} : { session: options.session }),
  });
  const onUnauthorized = vi.fn();
  const client = new ApiClient({ fetch: mock.fetch, onUnauthorized });
  return { mock, client, onUnauthorized };
}

async function jsonRequest(
  fetch: (input: string, init: RequestInit) => Promise<Response>,
  path: string,
  method: string,
  value: unknown,
  csrf = true,
): Promise<Response> {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (csrf) headers.set('X-MSJ-CSRF', '1');
  return fetch(path, { method, headers, body: JSON.stringify(value) });
}

async function signIn(client: ApiClient): Promise<void> {
  await client.signIn('validation-test-token');
}

describe('journal API mock validation and boundary behavior', () => {
  it('serves public health, applies auth before CSRF, and validates session mutations', async () => {
    const { mock, client, onUnauthorized } = setup();
    await expect(mock.fetch('/api/v1/health', { method: 'GET' })).resolves.toMatchObject({ status: 200 });
    const unauthorized = await mock.fetch('/api/v1/entries', { method: 'POST', body: '{}' });
    expect(unauthorized.status).toBe(401);
    expect(await unauthorized.json()).toMatchObject({ title: 'Unauthorized', detail: 'Supply a valid bearer token.' });
    expect(onUnauthorized).not.toHaveBeenCalled();

    const sessionWithoutCsrf = await jsonRequest(mock.fetch, '/api/v1/session', 'POST', { token: 'valid' }, false);
    expect(sessionWithoutCsrf.status).toBe(403);
    expect(await sessionWithoutCsrf.json()).toMatchObject({ code: 'CSRF_HEADER_MISSING' });
    await signIn(client);
    await expect(client.sessionProblem()).resolves.toBeNull();
    await client.signOut();
    await expect(client.sessionProblem()).resolves.toMatchObject({ status: 401, code: 'UNAUTHORIZED' });
  });

  it('returns specific route and method errors, including Allow', async () => {
    const { mock } = setup({ signedIn: true });
    const missing = await mock.fetch('/api/v1/no-such-route', { method: 'GET' });
    expect(missing.status).toBe(404);
    expect(missing.headers.get('Content-Type')).toContain('application/problem+json');
    const wrongMethod = await mock.fetch('/api/v1/health', { method: 'POST', headers: { 'X-MSJ-CSRF': '1' } });
    expect(wrongMethod.status).toBe(405);
    expect(wrongMethod.headers.get('Allow')).toBe('GET');
  });

  it('rejects invalid list bounds, non-integer values, views, orders, and malformed bodies', async () => {
    const { mock, client } = setup({ signedIn: true });
    for (const query of ['limit=0', 'limit=501', 'limit=1.5', 'offset=-1', 'offset=2.5', 'view=compact', 'order=sideways']) {
      const response = await mock.fetch(`/api/v1/entries?${query}`, { method: 'GET' });
      expect(response.status, query).toBe(422);
    }
    const malformedCreate = await mock.fetch('/api/v1/entries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-MSJ-CSRF': '1' },
      body: '{',
    });
    expect(malformedCreate.status).toBe(422);
    const existing = await client.createTextEntry({ text: 'Existing entry.', title: null, tags: [], entry_date: '2026-11-08' });
    const malformedPatch = await mock.fetch(`/api/v1/entries/${existing.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-MSJ-CSRF': '1' },
      body: '{',
    });
    expect(malformedPatch.status).toBe(400);
    await expect(client.listEntries({ filters: {}, order: 'desc', limit: 100, offset: 0 })).resolves.toMatchObject({
      items: [expect.objectContaining({ id: existing.id })], total: 1,
    });
  });

  it('validates patch fields, whitespace and nullable values intentionally', async () => {
    const { mock, client } = setup({ signedIn: true });
    const created = await client.createTextEntry({ text: 'A valid journal entry.', title: 'Original', tags: ['work'], entry_date: '2026-11-08' });
    for (const patch of [{}, { text: '   ' }, { text: null }, { tags: null }, { entry_date: null }, { unexpected: true }]) {
      const response = await jsonRequest(mock.fetch, `/api/v1/entries/${created.id}`, 'PATCH', patch);
      expect(response.status, JSON.stringify(patch)).toBe(422);
    }
    await expect(client.patchEntry(created.id, { title: null })).resolves.toMatchObject({ title: null, revision: 1 });
  });

  it('checks multipart media type, empty files, tags, and the upload limit', async () => {
    const { mock } = setup({ signedIn: true });
    const postForm = async (file: Blob, tags = '[]'): Promise<Response> => {
      const form = new FormData();
      form.append('file', new File([file], 'recording.webm', { type: file.type }));
      form.append('tags', tags);
      return mock.fetch('/api/v1/entries', { method: 'POST', headers: { 'X-MSJ-CSRF': '1' }, body: form });
    };
    expect((await postForm(new Blob(['audio'], { type: 'application/octet-stream' }))).status).toBe(415);
    expect((await postForm(new Blob([], { type: 'audio/webm' }))).status).toBe(422);
    expect((await postForm(new Blob(['audio'], { type: 'audio/webm' }), '{')).status).toBe(422);
    const oversized = new Blob([new Uint8Array(25 * 1024 * 1024 + 1)], { type: 'audio/webm' });
    expect((await postForm(oversized)).status).toBe(413);
  });

  it('applies date and facet filters with OR within each kind, AND across kinds, and scopes neighbors', async () => {
    const seeds = [
      fixture({ days_ago: 0, title: 'Today dummy', accomplishments: [] }),
      fixture({ days_ago: 1, title: 'Yesterday dummy', accomplishments: [] }),
      fixture({ days_ago: 30, tags: ['work'], projects: ['Atlas'], skills: ['TypeScript'] }),
      fixture({ days_ago: 40, title: 'Second note', tags: ['home'], projects: ['Beacon'], skills: ['Go'] }),
      fixture({ days_ago: 50, title: 'Third note', tags: ['home'], projects: ['Atlas'], skills: ['Go'] }),
    ] as const;
    const { client, mock } = setup({ seeds, signedIn: true });
    const filtered = await client.listEntries({
      filters: { from: '2026-09-01', to: '2026-10-31', tag: ['WORK', 'HOME'], project: ['beacon', 'ATLAS'], skill: ['go', 'typescript'] },
      order: 'asc', limit: 10, offset: 0,
    });
    expect(filtered.total).toBe(3);
    const page = await client.listEntries({ filters: { project: ['Beacon'], skill: ['TypeScript'] }, order: 'asc', limit: 10, offset: 0 });
    expect(page.total).toBe(0);
    const first = filtered.items[0];
    expect(first).toBeDefined();
    if (first === undefined) throw new Error('filter fixture includes entries');
    const neighbors = await client.neighbors(first.id, { tag: ['work', 'home'] });
    expect(neighbors.older).toBeNull();
    expect(neighbors.newer).toBe(filtered.items[1]?.id);
    const fullResponse = await mock.fetch('/api/v1/entries?view=full&limit=1', { method: 'GET' });
    expect(fullResponse.status).toBe(200);
    const fullPage = await fullResponse.json() as { readonly items: readonly unknown[] };
    expect(entryDetail(fullPage.items[0], 'items[0]').jobs.length).toBeGreaterThan(0);
  });

  it('validates export ids and renders JSON and Markdown with field selection', async () => {
    const { mock, client } = setup({ seeds: [fixture()], signedIn: true });
    const page = await client.listEntries({ filters: {}, order: 'desc', limit: 10, offset: 0 });
    const entry = page.items[0];
    expect(entry).toBeDefined();
    if (entry === undefined) throw new Error('export fixture exists');
    const include = { title: true, date: true, tags: true, summary: true, text: true, extraction: true } as const;
    await expect(client.exportEntries({ entry_ids: ['missing-id'], format: 'json', include, order: 'asc' })).rejects.toMatchObject({ status: 404 });
    for (const entryIds of [[], Array.from({ length: 1001 }, () => entry.id)]) {
      const response = await jsonRequest(mock.fetch, '/api/v1/export', 'POST', { entry_ids: entryIds, format: 'json', include, order: 'asc' });
      expect(response.status).toBe(422);
    }
    await client.runBatch();
    await mock.idle();
    const jsonExport = await client.exportEntries({
      entry_ids: [entry.id], format: 'json', order: 'asc',
      include: { title: false, date: false, tags: false, summary: false, text: false, extraction: true },
    });
    expect(jsonExport.contentType).toBe('application/json');
    expect(JSON.parse(jsonExport.body) as unknown).toMatchObject([{ id: entry.id, extraction: { projects: ['Atlas'] } }]);
    const markdown = await client.exportEntries({
      entry_ids: [entry.id], format: 'markdown', order: 'asc',
      include: { title: true, date: true, tags: false, summary: false, text: false, extraction: false },
    });
    expect(markdown.contentType).toBe('text/markdown; charset=utf-8');
    expect(markdown.body).toBe('## 2026-09-29 Build notes');
  });

  it('returns retry conflicts and missing-job errors', async () => {
    const { client } = setup({ signedIn: true });
    await expect(client.retryJob('missing-job')).rejects.toMatchObject({ status: 404 });
    const created = await client.createTextEntry({ text: 'Queued entry.', title: null, tags: [], entry_date: '2026-11-08' });
    const queuedJob = created.jobs[0];
    expect(queuedJob).toBeDefined();
    if (queuedJob === undefined) throw new Error('created entry contains queued jobs');
    await expect(client.retryJob(queuedJob.id)).rejects.toMatchObject({ status: 409 });
  });

  it('shares signed-in state through the configured session adapter', async () => {
    let persisted = false;
    const session = { get: (): boolean => persisted, set: (value: boolean): void => { persisted = value; } };
    const first = setup({ session });
    await signIn(first.client);
    expect(persisted).toBe(true);
    const second = setup({ session });
    await expect(second.client.sessionProblem()).resolves.toBeNull();
    await first.client.signOut();
    await expect(second.client.sessionProblem()).resolves.toMatchObject({ status: 401, code: 'UNAUTHORIZED' });
  });

  it('advances scheduled batches at the heartbeat and marks stale workers unavailable', async () => {
    let clock = FIXED_NOW;
    const scheduled = setup({ now: () => clock, signedIn: true });
    const before = await scheduled.client.queue();
    expect(before.worker.interval_s).toBe(900);
    expect(before.worker.next_run_at).toBe(new Date(clock + 11 * 60_000).toISOString());
    clock += 12 * 60_000;
    await scheduled.client.queue();
    await scheduled.mock.idle();
    await expect(scheduled.client.queue()).resolves.toMatchObject({ batch: { running: false, trigger: 'schedule' } });
    const stale = setup({ now: () => clock, workerStale: true, signedIn: true });
    const snapshot = await stale.client.queue();
    expect(snapshot.worker.next_run_at).toBeNull();
    expect(Date.parse(snapshot.worker.last_seen_at ?? '')).toBeLessThan(clock - 2 * 900_000);
  });

  it('aborts search after a tier and during request latency, and idle waits for running timers', async () => {
    const streamed = setup({ searchDelayMs: 10, signedIn: true });
    const controller = new AbortController();
    const search = streamed.client.search({ q: 'atlas', filters: {}, tiers: [1, 2, 3] }, (event) => {
      if (event.type === 'tier' && event.tier === 1) controller.abort();
    }, controller.signal);
    await expect(search).rejects.toMatchObject({ name: 'AbortError' });
    await streamed.mock.idle();

    const delayed = setup({ latencyMs: 100, signedIn: true });
    const delayedController = new AbortController();
    const request = delayed.client.facets(delayedController.signal);
    delayedController.abort();
    await expect(request).rejects.toMatchObject({ name: 'AbortError' });
    await delayed.mock.idle();
  });

  it('does not publish stale artifacts when an entry is revised or deleted during a batch', async () => {
    vi.useFakeTimers();
    try {
      const { client, mock } = setup({ jobDelayMs: 15, pickupDelayMs: 0, signedIn: true });
      const revisedSource = await client.createTextEntry({ text: 'Original source text.', title: 'Revision race', tags: [], entry_date: '2026-11-08' });
      await client.runBatch();
      await vi.advanceTimersByTimeAsync(2);
      expect((await client.queue()).counts.running).toBeGreaterThan(0);
      const revised = await client.patchEntry(revisedSource.id, { text: 'Current source text.' });
      expect(revised.revision).toBe(2);
      await vi.advanceTimersByTimeAsync(1000);
      await mock.idle();
      const current = await client.getEntry(revisedSource.id);
      expect(current.artifacts.filter((artifact) => artifact.revision === 2).map((artifact) => artifact.kind)).toEqual(['user_text']);
      expect(current.jobs.every((job) => job.revision === 2 && job.status === 'queued')).toBe(true);
      await client.runBatch();
      await vi.advanceTimersByTimeAsync(1000);
      await mock.idle();

      const deleted = await client.createTextEntry({ text: 'Delete during batch.', title: 'Deletion race', tags: [], entry_date: '2026-11-08' });
      await client.runBatch();
      await vi.advanceTimersByTimeAsync(2);
      expect((await client.queue()).counts.running).toBeGreaterThan(0);
      await client.deleteEntry(deleted.id);
      await vi.advanceTimersByTimeAsync(1000);
      await mock.idle();
      await expect(client.getEntry(deleted.id)).rejects.toMatchObject({ status: 404 });
    } finally {
      vi.useRealTimers();
    }
  });

  it('builds typed compilation results with evidence dates and schema fields', async () => {
    const seeds = [
      fixture({ days_ago: 0, title: 'Today dummy', accomplishments: [] }),
      fixture({ days_ago: 1, title: 'Yesterday dummy', accomplishments: [] }),
      fixture({ days_ago: 35, title: 'Atlas implementation' }),
      fixture({ days_ago: 45, title: 'Service review', accomplishments: [{ description: 'Cut latency', metrics: ['18 ms'] }] }),
    ] as const;
    const { client, mock } = setup({ seeds, jobDelayMs: 0, signedIn: true });
    const entries = await client.listEntries({
      filters: { from: '2026-09-20', to: '2026-10-10' }, order: 'asc', limit: 10, offset: 0,
    });
    const ids = entries.items.map((entry) => entry.id);
    const compilation = await client.createCompilation({ entry_ids: ids, purpose: 'portfolio', period: 'Autumn' });
    await client.runBatch();
    await mock.idle();
    const done = await client.getCompilation(compilation.id);
    expect(done.result).not.toBeNull();
    const serviceDate = entries.items.find((entry) => entry.title === 'Service review')?.entry_date;
    expect(done.result?.resume_bullets).toEqual(expect.arrayContaining([
      expect.objectContaining({ text: 'Cut latency (18 ms)', evidence_dates: [serviceDate] }),
    ]));
    expect(done.result?.technical_topics.every((topic) => topic.depth_notes.length > 0 && topic.evidence_dates.length > 0)).toBe(true);
    expect(done.result?.star_stories.every((story) => story.title.length > 0 && story.evidence_dates.length > 0)).toBe(true);
  });
});
