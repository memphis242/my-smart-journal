import { describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import { extraction } from '../api/guards';
import type { SeedConcept, SeedEntry } from './seed-types';
import { createMockApi } from './server';

const TODAY = Date.parse('2026-11-08T18:00:00.000Z');

function seed(overrides: Partial<SeedEntry> = {}): SeedEntry {
  return {
    days_ago: 50,
    time: '09:30',
    source: 'text',
    title: 'Atlas latency review',
    tags: ['Work', 'journal'],
    raw: 'I reduced Atlas latency with TypeScript. The service now returns in 18 ms and feels fast.',
    cleaned: 'I reduced Atlas latency with TypeScript. The service now returns in 18 ms and feels fast.',
    summary: 'I reduced Atlas latency with TypeScript.',
    projects: ['Atlas'],
    skills: ['TypeScript'],
    accomplishments: [{ description: 'Reduced latency', metrics: ['18 ms'] }],
    people_roles: [{ name: 'Mina', role: 'reviewer' }],
    star_fragments: [{ situation: 'A slow endpoint', task: 'Improve it', action: 'Reduced allocations', result: '18 ms response time' }],
    ...overrides,
  };
}

const SEEDS: readonly SeedEntry[] = [
  seed({ days_ago: 0, title: 'Today note', tags: ['work'], raw: 'Planning Atlas search tomorrow.', cleaned: 'Planning Atlas search tomorrow.', summary: 'Planning Atlas search tomorrow.', accomplishments: [] }),
  seed({ days_ago: 1, title: 'Yesterday note', raw: 'I am waiting for the Atlas review.', cleaned: 'I am waiting for the Atlas review.', summary: 'I am waiting for the Atlas review.', accomplishments: [] }),
  seed({ days_ago: 3, title: 'Transient summary', raw: 'The Atlas service improved.', cleaned: 'The Atlas service improved.', summary: 'The Atlas service improved.', accomplishments: [] }),
  seed({ days_ago: 5, source: 'audio', title: 'Voice memo', raw: 'Um, the audio transcript mentions a project.', cleaned: 'The audio transcript mentions a project.', summary: 'The audio transcript mentions a project.', accomplishments: [] }),
  seed({ days_ago: 8, title: 'Review concern', raw: 'I increased latency from 18 ms to 21 ms while changing Atlas.', cleaned: 'I increased latency from 18 ms to 21 ms while changing Atlas.', summary: 'I increased latency from 18 ms to 21 ms while changing Atlas.', accomplishments: [{ description: 'Measured latency', metrics: ['18 ms to 21 ms'] }] }),
  seed({ days_ago: 15, title: null, tags: ['Work'], raw: 'I delivered a dashboard using TypeScript.', cleaned: 'I delivered a dashboard using TypeScript.', summary: 'I delivered a dashboard using TypeScript.', accomplishments: [] }),
  seed({ days_ago: 20, title: 'Lowercase note', tags: ['notes'], raw: 'We discussed atlas changes yesterday.', cleaned: 'We discussed atlas changes yesterday.', summary: 'We discussed atlas changes yesterday.', projects: [], skills: [], accomplishments: [] }),
  seed({ days_ago: 50 }),
];

const CONCEPTS: readonly SeedConcept[] = [
  { name: 'performance', words: ['latency', 'speed', 'fast'] },
  { name: 'planning', words: ['tomorrow', 'next', 'plan'] },
];

function setup(options: Partial<Parameters<typeof createMockApi>[0]> = {}) {
  const mock = createMockApi({
    seeds: SEEDS,
    compilations: [],
    concepts: CONCEPTS,
    now: () => TODAY,
    ...options,
  });
  const onUnauthorized = vi.fn();
  const client = new ApiClient({ fetch: mock.fetch, onUnauthorized });
  return { mock, client, onUnauthorized };
}

async function signIn(client: ApiClient): Promise<void> {
  await client.signIn('local-test-token');
}

describe('journal API mock through ApiClient', () => {
  it('supports sign-in, rejects bad tokens, and reports an expired session once', async () => {
    const { client, onUnauthorized } = setup();
    expect(await client.sessionProblem()).toMatchObject({ status: 401, code: 'UNAUTHORIZED' });
    await expect(client.facets()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    await expect(client.signIn('wrong')).rejects.toMatchObject({ code: 'BAD_TOKEN', status: 401 });
    await client.signIn('local-test-token');
    expect(await client.sessionProblem()).toBeNull();
    await client.signOut();
    await expect(client.facets()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(2);
  });

  it('rate limits repeated failed sign-ins and enforces the CSRF header after auth', async () => {
    let clock = TODAY;
    const { mock, client } = setup({ now: () => clock });
    for (let attempt = 0; attempt < 5; attempt += 1) {
      await expect(client.signIn('wrong')).rejects.toMatchObject({ code: 'BAD_TOKEN' });
    }
    await expect(client.signIn('wrong')).rejects.toMatchObject({ code: 'RATE_LIMITED', retryAfterS: 60 });
    clock += 61_000;
    await signIn(client);
    const response = await mock.fetch('/api/v1/entries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'Missing CSRF header' }),
    });
    expect(response.status).toBe(403);
    expect(response.headers.get('Content-Type')).toContain('application/problem+json');
    await expect(response.json()).resolves.toMatchObject({ code: 'CSRF_HEADER_MISSING' });
  });

  it('filters, orders, pages, derives statuses, and guards detail artifacts', async () => {
    const { client } = setup();
    await signIn(client);
    const page = await client.listEntries({ filters: { tag: ['work'], project: ['atlas'], skill: ['typescript'] }, order: 'asc', limit: 1, offset: 0 });
    expect(page.total).toBe(4);
    expect(page.items).toHaveLength(1);
    expect(page.items[0]?.entry_date).toBe('2026-09-19');

    const all = await client.listEntries({ filters: {}, order: 'desc', limit: 100, offset: 0 });
    expect(all.items.map((entry) => entry.status)).toContain('failed');
    expect(all.items.map((entry) => entry.status)).toContain('needs_attention');
    expect(all.items.filter((entry) => entry.status === 'queued').length).toBeGreaterThanOrEqual(3);
    expect(all.items[0]?.entry_date).toBe('2026-11-08');

    const failed = all.items.find((entry) => entry.status === 'failed');
    expect(failed).toBeDefined();
    if (failed === undefined) throw new Error('fixture must contain failed audio entry');
    const detail = await client.getEntry(failed.id);
    expect(detail.source).toBe('audio');
    const extractionArtifact = detail.artifacts.find((artifact) => artifact.kind === 'extraction');
    expect(extractionArtifact).toBeUndefined();
    const completed = all.items.find((entry) => entry.title === 'Atlas latency review');
    expect(completed).toBeDefined();
    if (completed === undefined) throw new Error('fixture must contain a processed entry');
    const completedDetail = await client.getEntry(completed.id);
    const artifact = completedDetail.artifacts.find((item) => item.kind === 'extraction');
    expect(artifact).toBeDefined();
    if (artifact === undefined) throw new Error('processed fixture must contain extraction artifact');
    expect(extraction(JSON.parse(artifact.content) as unknown, 'extraction').projects).toContain('Atlas');
    expect(completedDetail.review_sessions.cleanup?.status).toBe('accepted');
    const reverse = await client.listEntries({ filters: { from: '2026-09-01', to: '2026-10-31' }, order: 'desc', limit: 2, offset: 1 });
    expect(reverse.total).toBe(4);
    expect(reverse.items).toHaveLength(2);
  });

  it('returns filtered neighbors at both ends of the ordering', async () => {
    const { client } = setup();
    await signIn(client);
    const asc = await client.listEntries({ filters: {}, order: 'asc', limit: 100, offset: 0 });
    const first = asc.items[0];
    const last = asc.items.at(-1);
    expect(first).toBeDefined();
    expect(last).toBeDefined();
    if (first === undefined || last === undefined) throw new Error('fixture entries are required');
    await expect(client.neighbors(first.id, {})).resolves.toMatchObject({ newer: asc.items[1]?.id, older: null });
    await expect(client.neighbors(last.id, {})).resolves.toMatchObject({ newer: null, older: asc.items.at(-2)?.id });
  });

  it('creates text and audio entries, revises text into a queued revision, then deletes', async () => {
    const { client } = setup();
    await signIn(client);
    const text = await client.createTextEntry({ text: 'I built a new Atlas index.', title: 'New work', tags: ['Work'], entry_date: '2026-11-07' });
    expect(text.status).toBe('queued');
    expect(text.revision).toBe(1);
    expect(text.jobs.length).toBeGreaterThan(0);
    const audio = await client.createAudioEntry({ audio: new Blob(['voice data'], { type: 'audio/webm' }), mimeType: 'audio/webm', fileName: 'note.webm', title: 'Voice', tags: ['audio'], entry_date: '2026-11-06' });
    expect(audio.source).toBe('audio');
    expect(audio.status).toBe('queued');
    const revised = await client.patchEntry(text.id, { text: 'I redesigned the Atlas index.' });
    expect(revised.revision).toBe(2);
    expect(revised.status).toBe('queued');
    expect(revised.jobs.every((job) => job.revision === 2)).toBe(true);
    await client.deleteEntry(text.id);
    await expect(client.getEntry(text.id)).rejects.toMatchObject({ status: 404 });
  });

  it('groups facet values by spelling frequency and streams ordered, deduplicated search tiers', async () => {
    const { client } = setup();
    await signIn(client);
    const facets = await client.facets();
    const workFacet = facets.tags.find((item) => item.value === 'Work');
    expect(workFacet?.count).toBeGreaterThan(0);
    const events: { type: string; tier?: number; results?: readonly { entry_id: string; passage: string | null }[] }[] = [];
    await client.search({ q: 'Atlas', filters: {}, tiers: [1, 2, 3, 4, 5] }, (event) => {
      if (event.type === 'tier') events.push({ type: 'tier', tier: event.tier, results: event.results });
      else events.push({ type: 'done' });
    });
    expect(events.map((event) => event.tier).filter((tier) => tier !== undefined)).toEqual([1, 2, 3, 4, 5]);
    expect(events.at(-1)?.type).toBe('done');
    const hits = events.flatMap((event) => event.results ?? []);
    expect(new Set(hits.map((hit) => hit.entry_id)).size).toBe(hits.length);
    expect(events[1]?.results?.length).toBeGreaterThan(0);
    const subset: number[] = [];
    await client.search({ q: 'Atlas', filters: {}, tiers: [1, 2] }, (event) => {
      if (event.type === 'tier') subset.push(event.tier);
    });
    expect(subset).toEqual([1, 2]);
    const semanticEvents: { passage: string | null }[] = [];
    await client.search({ q: 'fast', filters: {}, tiers: [5] }, (event) => {
      if (event.type === 'tier') semanticEvents.push(...event.results);
    });
    expect(semanticEvents.length).toBeGreaterThan(0);
    expect(semanticEvents.every((hit) => hit.passage !== null)).toBe(true);
  });

  it.each([
    ['disabled', 'EMBEDDINGS_DISABLED'],
    ['model_load_failed', 'EMBEDDING_MODEL_LOAD_FAILED'],
    ['dependency_missing', 'EMBEDDING_DEPENDENCY_MISSING'],
    ['encoding_failed', 'EMBEDDING_ENCODING_FAILED'],
  ] as const)('reports semantic unavailability when embeddings are %s', async (embeddings, code) => {
    const { client } = setup({ embeddings });
    await signIn(client);
    let unavailable: string | null = null;
    await client.search({ q: 'latency', filters: {}, tiers: [5] }, (event) => {
      if (event.type === 'tier') unavailable = event.unavailable?.code ?? null;
    });
    expect(unavailable).toBe(code);
  });

  it('retrieves ranked entries and reports pending embeddings', async () => {
    const { client } = setup();
    await signIn(client);
    const result = await client.retrieve({ query: 'latency performance', k: 5, filters: {} });
    expect(result.items.length).toBeGreaterThan(0);
    expect(result.items[0]?.score).toBeGreaterThan(0);
    expect(result.items[0]?.passages.length).toBeGreaterThan(0);
    expect(result.unavailable?.code).toBe('EMBEDDINGS_PENDING');
  });

  it('shows the queue, rejects a concurrent batch, drains jobs, and retries a failed job', async () => {
    let clock = TODAY;
    const { client, mock } = setup({ jobDelayMs: 10, now: () => clock });
    await signIn(client);
    const initial = await client.queue();
    expect(initial.batch.running).toBe(false);
    expect(initial.worker.interval_s).toBe(86_400);
    expect(initial.worker.stale).toBe(false);
    expect(initial.counts.failed).toBeGreaterThan(0);
    const start = await client.runBatch();
    expect(start.requested).toBe(true);
    await mock.idle();
    const after = await client.queue();
    expect(after.batch.running).toBe(false);
    const page = await client.listEntries({ filters: {}, order: 'desc', limit: 100, offset: 0 });
    expect(page.items.filter((entry) => entry.status === 'queued')).toHaveLength(1);
    const failed = page.items.find((entry) => entry.status === 'failed');
    expect(failed).toBeDefined();
    if (failed === undefined) throw new Error('transcription failure must remain visible');
    const detail = await client.getEntry(failed.id);
    const failedJob = detail.jobs.find((job) => job.status === 'failed');
    expect(failedJob?.kind).toBe('transcribe');
    if (failedJob === undefined) throw new Error('failed transcription job is required');
    const retried = await client.retryJob(failedJob.id);
    expect(retried.status).toBe('queued');
    expect(retried.attempts).toBe(0);
    clock += 7 * 60 * 1000;
    await client.runBatch();
    await mock.idle();
    await expect(client.getEntry(failed.id)).resolves.toMatchObject({ status: 'done' });
    const created = await client.createTextEntry({ text: 'A completed work item.', title: 'Completes in batch', tags: [], entry_date: '2026-11-08' });
    await client.runBatch();
    await mock.idle();
    await expect(client.getEntry(created.id)).resolves.toMatchObject({ status: 'done' });
  });

  it('exports exact plain text and completes a compilation lifecycle', async () => {
    const compilationSeed = {
      days_ago: 4,
      purpose: 'career update',
      period: 'November',
      entries: [2, 4],
      resume_bullets: [{ text: 'Reduced latency to 18 ms', evidence: [2] }],
      star_stories: [],
      technical_topics: [{ topic: 'TypeScript', depth_notes: 'Used in production.', evidence: [2] }],
      open_threads: [],
    };
    const { client, mock } = setup({ compilations: [compilationSeed], jobDelayMs: 0 });
    await signIn(client);
    const page = await client.listEntries({ filters: {}, order: 'asc', limit: 100, offset: 0 });
    const first = page.items.find((entry) => entry.title === 'Atlas latency review');
    const second = page.items.find((entry) => entry.title === null && entry.entry_date === '2026-10-24');
    expect(first).toBeDefined();
    expect(second).toBeDefined();
    if (first === undefined || second === undefined) throw new Error('export fixture entries are required');
    const exported = await client.exportEntries({
      entry_ids: [first.id, second.id],
      format: 'text',
      include: { title: true, date: true, tags: true, summary: true, text: true, extraction: false },
      order: 'asc',
    });
    expect(exported.contentType).toBe('text/plain; charset=utf-8');
    expect(exported.filename).toMatch(/^journal-export-\d{4}-\d{2}-\d{2}\.txt$/);
    expect(exported.body).toBe([
      '2026-09-19  Atlas latency review',
      '',
      'Summary:',
      'I reduced Atlas latency with TypeScript.',
      '',
      'I reduced Atlas latency with TypeScript. The service now returns in 18 ms and feels fast.',
      '',
      'Tags: Work, journal',
      '',
      '--------------------',
      '',
      '2026-10-24',
      '',
      'Summary:',
      'I delivered a dashboard using TypeScript.',
      '',
      'I delivered a dashboard using TypeScript.',
      '',
      'Tags: Work',
    ].join('\n'));

    const created = await client.createCompilation({ entry_ids: [first.id, second.id], purpose: 'career update', period: 'November' });
    expect(created.status).toBe('queued');
    expect((await client.listCompilations())[0]?.id).toBe(created.id);
    await client.runBatch();
    await mock.idle();
    const done = await client.getCompilation(created.id);
    expect(done.status).toBe('done');
    expect(done.result?.resume_bullets.length).toBeGreaterThan(0);
    await client.deleteCompilation(created.id);
    await expect(client.getCompilation(created.id)).rejects.toMatchObject({ status: 404 });
  });

  it('aborts a delayed search stream with AbortError', async () => {
    const { client } = setup({ searchDelayMs: 20 });
    await signIn(client);
    const controller = new AbortController();
    const events = vi.fn();
    const search = client.search({ q: 'Atlas', filters: {}, tiers: [1, 2, 3, 4, 5] }, events, controller.signal);
    controller.abort();
    await expect(search).rejects.toMatchObject({ name: 'AbortError' });
  });
});
