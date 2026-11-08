import { describe, expect, it, vi } from 'vitest';
import { ApiClient, attachmentName, buildQuery, type FetchLike } from './client';
import { ApiError } from './errors';
import type { SearchEvent } from './types';

interface Call {
  readonly url: string;
  readonly init: RequestInit;
}

function client(respond: (call: Call) => Response | Promise<Response>): { api: ApiClient; calls: Call[]; unauthorized: ReturnType<typeof vi.fn> } {
  const calls: Call[] = [];
  const unauthorized = vi.fn();
  const fetch: FetchLike = (url, init) => {
    const call = { url, init };
    calls.push(call);
    return Promise.resolve(respond(call));
  };
  return { api: new ApiClient({ fetch, onUnauthorized: unauthorized }), calls, unauthorized };
}

function problem(status: number, body: Record<string, unknown>, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify({ type: 'about:blank', status, ...body }), {
    status,
    headers: { 'Content-Type': 'application/problem+json', ...headers },
  });
}

const QUEUE = {
  batch: { running: false, started_at: null, trigger: null, last_finished_at: null, last_result: null, last_error: null },
  worker: { last_seen_at: '2026-11-08T10:00:00Z', interval_s: 900, next_run_at: '2026-11-08T10:15:00Z' },
  counts: { queued: 0, running: 0, failed: 0, blocked: 0 },
  items: [],
};

describe('ApiClient requests', () => {
  it('sends the CSRF header on mutations only, and repeats facet filters', async () => {
    const { api, calls } = client((call) =>
      call.init.method === 'GET'
        ? new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 })
        : new Response(JSON.stringify({ requested: true }), { status: 202 }),
    );
    await api.listEntries({ filters: { from: '2026-11-01', tag: ['work', 'school'] }, order: 'desc', limit: 100, offset: 0 });
    await api.runBatch();
    const [get, post] = calls;
    expect(new Headers(get?.init.headers).has('X-MSJ-CSRF')).toBe(false);
    expect(get?.url).toBe('/api/v1/entries?from=2026-11-01&tag=work&tag=school&view=summary&order=desc&limit=100&offset=0');
    expect(new Headers(post?.init.headers).get('X-MSJ-CSRF')).toBe('1');
    expect(post?.init.credentials).toBe('same-origin');
  });

  it('builds query strings without undefined values', () => {
    expect(buildQuery({ q: 'a b', from: undefined, tag: [] })).toBe('?q=a+b');
    expect(buildQuery({})).toBe('');
  });
});

describe('ApiClient errors', () => {
  it('keeps the server code, detail and Retry-After', async () => {
    const { api } = client(() => problem(429, { title: 'Too Many Requests', detail: 'Slow down.', code: 'RATE_LIMITED' }, { 'Retry-After': '60' }));
    const error = await api.signIn('t0ken').catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.code).toBe('RATE_LIMITED');
    expect(apiError.retryAfterS).toBe(60);
    expect(apiError.message).toContain('Slow down.');
    expect(apiError.hint).toContain('60 s');
  });

  it('derives a code from the status when the body has none, and keeps a non-JSON body in the details', async () => {
    const { api } = client(() => new Response('<html>Bad gateway</html>', { status: 502, statusText: 'Bad Gateway' }));
    const error = (await api.queue().catch((caught: unknown) => caught)) as ApiError;
    expect(error.code).toBe('SERVER_UNAVAILABLE');
    expect(error.details.some((line) => line.label === 'Body' && line.value.includes('Bad gateway'))).toBe(true);
  });

  it('reports an unreachable server as NETWORK_UNREACHABLE with the cause', async () => {
    const api = new ApiClient({ fetch: () => Promise.reject(new TypeError('Failed to fetch')), onUnauthorized: vi.fn() });
    const error = (await api.queue().catch((caught: unknown) => caught)) as ApiError;
    expect(error.code).toBe('NETWORK_UNREACHABLE');
    expect(error.details.find((line) => line.label === 'Cause')?.value).toBe('Failed to fetch');
  });

  it('signals a lost session on 401, but not for the sign-in probes', async () => {
    const { api, unauthorized } = client(() => problem(401, { title: 'Unauthorized', detail: 'Supply a valid bearer token.', code: 'TOKEN_REISSUE_REQUIRED' }));
    const probe = await api.sessionProblem();
    expect(probe?.code).toBe('TOKEN_REISSUE_REQUIRED');
    expect(unauthorized).not.toHaveBeenCalled();
    await api.facets().catch(() => undefined);
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });

  it('names the field when a response does not match the contract', async () => {
    const { api } = client(() => new Response(JSON.stringify({ ...QUEUE, counts: { ...QUEUE.counts, queued: '3' } }), { status: 200 }));
    const error = (await api.queue().catch((caught: unknown) => caught)) as ApiError;
    expect(error.code).toBe('RESPONSE_INVALID');
    expect(error.details.find((line) => line.label === 'Field')?.value).toBe('counts.queued');
  });
});

describe('ApiClient search stream', () => {
  function ndjson(lines: readonly string[]): Response {
    return new Response(lines.map((line) => `${line}\n`).join(''), { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } });
  }

  it('passes tier events in order and settles on the completion line', async () => {
    const { api } = client(() =>
      ndjson([
        '{"tier":1,"name":"exact","results":[{"entry_id":"a","score":1,"snippet":"x"}],"elapsed_ms":0.1}',
        '{"tier":5,"name":"semantic","results":[],"elapsed_ms":3,"unavailable":{"code":"EMBEDDINGS_DISABLED","detail":"off"}}',
        '{"done":true,"elapsed_ms":4}',
      ]),
    );
    const events: SearchEvent[] = [];
    await api.search({ q: 'x', filters: {}, tiers: [1, 5] }, (event) => events.push(event));
    expect(events.map((event) => (event.type === 'tier' ? event.tier : 'done'))).toEqual([1, 5, 'done']);
    const semantic = events[1];
    expect(semantic?.type === 'tier' ? semantic.unavailable?.code : null).toBe('EMBEDDINGS_DISABLED');
  });

  it('fails with STREAM_INCOMPLETE when the completion line never arrives', async () => {
    const { api } = client(() => ndjson(['{"tier":1,"name":"exact","results":[],"elapsed_ms":0.1}']));
    const error = (await api.search({ q: 'x', filters: {}, tiers: [1] }, () => undefined).catch((caught: unknown) => caught)) as ApiError;
    expect(error.code).toBe('STREAM_INCOMPLETE');
  });
});

describe('attachmentName', () => {
  it('reads plain and RFC 5987 file names', () => {
    expect(attachmentName('attachment; filename="journal-export-2026-11-08.txt"', 'x')).toBe('journal-export-2026-11-08.txt');
    expect(attachmentName("attachment; filename*=UTF-8''journal%20export.md", 'x')).toBe('journal export.md');
    expect(attachmentName(null, 'fallback.json')).toBe('fallback.json');
  });
});
