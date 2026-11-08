import type { FetchLike } from '../api/client';
import type { EntryFilters, EntrySummary, ExportInclude, ExportRequest, SearchTier } from '../api/types';
import { assert } from '../lib/assert';
import { exportResponse } from './export';
import { abortError, calendarDate, HttpProblem, invalid, json, localDate, record, strings, Tasks } from './http';
import { Pipeline } from './pipeline';
import { retrieve, semanticUnavailable, tierResults, type SearchDocument } from './search';
import type { SeedCompilation, SeedConcept, SeedEntry } from './seed-types';
import { compareEntries, Store, type StoredCompilation, type StoredEntry } from './state';

export interface MockOptions {
  readonly seeds: readonly SeedEntry[];
  readonly compilations: readonly SeedCompilation[];
  readonly concepts: readonly SeedConcept[];
  readonly now?: () => number;
  readonly latencyMs?: number;
  readonly jobDelayMs?: number;
  /** Delay between accepting a batch request and the worker picking it up. */
  readonly pickupDelayMs?: number;
  /** Seeds a historical batch failure; future batches run normally. */
  readonly batchError?: boolean;
  readonly searchDelayMs?: number;
  readonly workerStale?: boolean;
  readonly embeddings?: 'ok' | 'disabled' | 'model_unavailable';
  readonly signedIn?: boolean;
  /** Requires a successful sign-in before authenticated requests are accepted. */
  readonly tokenRotated?: boolean;
  readonly session?: {
    get(): boolean;
    set(value: boolean): void;
  };
}
export interface MockApi {
  readonly fetch: FetchLike;
  /** Resolves once no batch is running and no timers are pending. For tests. */
  idle(): Promise<void>;
}
const AUDIO_TYPES: ReadonlyMap<string, string> = new Map([
  ['audio/wav', 'wav'], ['audio/x-wav', 'wav'], ['audio/mpeg', 'mp3'], ['audio/mp4', 'm4a'],
  ['audio/webm', 'webm'], ['audio/ogg', 'ogg'], ['audio/flac', 'flac'], ['audio/x-flac', 'flac'],
]);
const TIER_NAMES = ['exact', 'substring', 'fuzzy', 'deterministic', 'semantic'] as const;
const MUTATIONS = new Set(['POST', 'PATCH', 'DELETE']);

async function body(request: Request, entryCreate = false): Promise<Readonly<Record<string, unknown>>> {
  let value: unknown;
  try { value = await request.json(); }
  catch {
    if (request.signal.aborted) throw abortError();
    if (entryCreate) invalid('Provide non-empty text and valid entry fields.');
    throw new HttpProblem(400, 'Bad Request', 'Body must be valid JSON.');
  }
  return record(value);
}
function title(value: unknown): string | null {
  if (value !== null && typeof value !== 'string') invalid('title must be a string or null.');
  return value;
}
function integer(value: unknown, minimum: number, maximum: number, detail: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < minimum || value > maximum) invalid(detail);
  return value;
}
function queryInteger(params: URLSearchParams, key: string, fallback: number, minimum: number, maximum: number): number {
  const raw = params.get(key);
  if (raw === null) return fallback;
  if (!/^\d+$/.test(raw)) invalid(`${key} must be an integer between ${minimum} and ${maximum}.`);
  return integer(Number(raw), minimum, maximum, `${key} must be an integer between ${minimum} and ${maximum}.`);
}
function queryFilters(params: URLSearchParams): EntryFilters {
  const from = params.get('from');
  const to = params.get('to');
  return { ...(from === null ? {} : { from: calendarDate(from) }), ...(to === null ? {} : { to: calendarDate(to) }),
    tag: params.getAll('tag'), project: params.getAll('project'), skill: params.getAll('skill') };
}
function bodyFilters(value: unknown): EntryFilters {
  if (value === undefined) return {};
  const filters = record(value, 'filters must be an object with from, to, tag, project, and skill fields.');
  const result: { from?: string; to?: string; tag?: readonly string[]; project?: readonly string[]; skill?: readonly string[] } = {};
  for (const field of ['from', 'to'] as const) if (filters[field] !== undefined) result[field] = calendarDate(filters[field]);
  for (const field of ['tag', 'project', 'skill'] as const) if (filters[field] !== undefined) result[field] = strings(filters[field], `filters.${field} must be an array of strings.`);
  return result;
}
function matches(values: readonly string[], selected: readonly string[] | undefined): boolean {
  return selected === undefined || selected.length === 0 || selected.some((wanted) => values.some((value) => value.toLowerCase() === wanted.toLowerCase()));
}
function filtered(store: Store, filters: EntryFilters): readonly EntrySummary[] {
  return [...store.entries.values()].map((entry) => store.summary(entry)).filter((entry) =>
    (filters.from === undefined || entry.entry_date >= filters.from) && (filters.to === undefined || entry.entry_date <= filters.to)
    && matches(entry.tags, filters.tag) && matches(entry.projects, filters.project) && matches(entry.skills, filters.skill)).sort(compareEntries);
}
function searchDocuments(store: Store, filters: EntryFilters): readonly SearchDocument[] {
  return filtered(store, filters).map((entry) => {
    const record = store.entry(entry.id);
    const cleaned = store.currentArtifacts(record).findLast((artifact) => artifact.kind === 'cleaned')?.content ?? '';
    const sentences = cleaned.split(/(?<=[.!?])\s+|\n\n/).filter((sentence) => sentence.trim() !== '');
    const chunks = sentences.map((_sentence, index) => sentences.slice(index, index + 3).join(' '));
    if (entry.summary !== null) chunks.push(entry.summary);
    return { entry, chunks, embedded: store.currentJobs(record).some((job) => job.kind === 'embed' && job.status === 'done') };
  });
}
function facet(values: readonly (readonly string[])[]): readonly { readonly value: string; readonly count: number }[] {
  const groups = new Map<string, { count: number; readonly spellings: Map<string, number> }>();
  for (const entry of values) {
    const present = new Set<string>();
    for (const value of entry) {
      const key = value.toLowerCase();
      const group = groups.get(key) ?? { count: 0, spellings: new Map<string, number>() };
      if (!present.has(key)) { group.count += 1; void present.add(key); }
      void group.spellings.set(value, (group.spellings.get(value) ?? 0) + 1); // Count casing to choose the most frequent spelling.
      void groups.set(key, group); // Each case-insensitive value owns one group.
    }
  }
  return [...groups.values()].map((group) => {
    const spelling = [...group.spellings].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0];
    assert(spelling !== undefined, 'facet group contains at least one spelling');
    return { value: spelling[0], count: group.count };
  }).sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
}
function tiers(params: URLSearchParams): readonly SearchTier[] {
  const raw = params.get('tiers') ?? '1,2,3,4,5';
  const parts = raw.split(',');
  if (parts.some((part) => !/^[1-5]$/.test(part))) invalid('tiers must be a comma-separated list of search tiers 1-5.');
  return [...new Set(parts.map((part) => Number(part) as SearchTier))];
}
function hasControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code <= 0x1f || code === 0x7f) return true;
  }
  return false;
}

function query(value: unknown, field: string): string {
  if (typeof value === 'string' && hasControlCharacters(value)) {
    throw new HttpProblem(
      422,
      'Validation Error',
      `${field} must not contain control characters. Remove tabs, line breaks, and other control characters, then search again.`,
      'INVALID_QUERY',
    );
  }
  if (typeof value !== 'string' || !value.trim() || value.length > 500) invalid(`${field} must contain 1-500 characters.`);
  return value;
}
function ids(store: Store, value: unknown, maximum = Number.MAX_SAFE_INTEGER): readonly string[] {
  const requested = strings(value, 'entry_ids must be an array of strings.');
  if (requested.length === 0 || requested.length > maximum) invalid(`entry_ids must contain 1-${maximum} ids.`);
  const missing = requested.filter((id) => !store.entries.has(id));
  if (missing.length > 0) throw new HttpProblem(404, 'Not Found', `Entries not found: ${missing.slice(0, 20).join(', ')}`, 'ENTRY_NOT_FOUND');
  return requested;
}
function compilationView(compilation: StoredCompilation): Omit<StoredCompilation, 'job_id'> {
  return structuredClone({ id: compilation.id, created_at: compilation.created_at, status: compilation.status, entry_ids: compilation.entry_ids,
    purpose: compilation.purpose, period: compilation.period, result: compilation.result, last_error: compilation.last_error,
    model: compilation.model, prompt_sha256: compilation.prompt_sha256 });
}
function unknownFields(payload: Readonly<Record<string, unknown>>, allowed: readonly string[]): void {
  const unknown = Object.keys(payload).filter((key) => !allowed.includes(key));
  if (unknown.length > 0) invalid(`Unsupported entry fields: ${unknown.join(', ')}. Use ${allowed.join(', ')}.`);
}

export function createMockApi(options: MockOptions): MockApi {
  const now = options.now ?? Date.now;
  const store = new Store(options.seeds, options.compilations, now);
  const tasks = new Tasks();
  const pipeline = new Pipeline(
    store,
    tasks,
    now,
    options.jobDelayMs ?? 0,
    options.workerStale ?? false,
    options.pickupDelayMs ?? 0,
    options.batchError ?? false,
  );
  const embeddings = options.embeddings ?? 'ok';
  let signedIn = options.signedIn ?? options.session?.get() ?? false;
  let tokenRotated = options.tokenRotated ?? false;
  if (options.signedIn !== undefined) options.session?.set(signedIn);
  let failures: readonly number[] = [];
  const setSession = (value: boolean): void => {
    signedIn = value;
    options.session?.set(value);
  };
  const isSignedIn = (): boolean => options.session?.get() ?? signedIn;
  const checkSignInRateLimit = (): void => {
    failures = failures.filter((time) => now() - time < 60000);
    if (failures.length >= 5) {
      throw new HttpProblem(
        429,
        'Too Many Requests',
        'Too many failed sign-in attempts. Wait 60 seconds before trying again.',
        'RATE_LIMITED',
        { 'Retry-After': '60' },
      );
    }
  };

  const search = (documents: readonly SearchDocument[], q: string, requested: readonly SearchTier[], signal: AbortSignal): Response => {
    const start = now();
    const all = tierResults(documents, q, options.concepts, embeddings === 'ok' ? requested : requested.filter((tier) => tier !== 5), now());
    const unavailable = semanticUnavailable(documents, embeddings);
    const encoder = new TextEncoder();
    let finished = false;
    const cancellation = new AbortController();
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    const abort = (): void => {
      if (finished) return;
      finished = true;
      cancellation.abort();
      streamController?.error(abortError());
      signal.removeEventListener('abort', abort);
    };
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
        signal.addEventListener('abort', abort, { once: true });
        if (signal.aborted) { abort(); return; }
        const send = async (): Promise<void> => {
          try {
            for (const tier of requested) {
              await tasks.delay((options.searchDelayMs ?? 0) * (tier === 5 ? 6 : 1), cancellation.signal);
              if (finished) return;
              const results = tier === 5 && embeddings !== 'ok' ? [] : all[tier - 1] ?? [];
              controller.enqueue(encoder.encode(`${JSON.stringify({ tier, name: TIER_NAMES[tier - 1], results, elapsed_ms: Math.max(0, now() - start),
                ...(tier === 5 && unavailable !== null ? { unavailable } : {}) })}\n`));
            }
            controller.enqueue(encoder.encode(`${JSON.stringify({ done: true, elapsed_ms: Math.max(0, now() - start) })}\n`));
            finished = true;
            controller.close();
          } catch (error) {
            if (!finished) { finished = true; controller.error(error); }
          } finally { signal.removeEventListener('abort', abort); }
        };
        tasks.track(send());
      },
      cancel() { finished = true; cancellation.abort(); signal.removeEventListener('abort', abort); },
    });
    return new Response(stream, { headers: { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no' } });
  };

  const route = async (request: Request): Promise<Response> => {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;
    const params = url.searchParams;
    const session = path === '/api/v1/session';
    const health = path === '/api/v1/health';
    if (!(session && method === 'POST') && !health) {
      if (tokenRotated) {
        throw new HttpProblem(
          401,
          'Unauthorized',
          'The access token was replaced with msj create-token; sign in with the new token.',
          'TOKEN_REISSUE_REQUIRED',
        );
      }
      if (!isSignedIn()) {
        throw new HttpProblem(401, 'Unauthorized', 'Supply a valid bearer token.');
      }
    }
    if (MUTATIONS.has(method) && request.headers.get('X-MSJ-CSRF') !== '1') throw new HttpProblem(403, 'Forbidden', 'Include X-MSJ-CSRF: 1 when changing journal data.', 'CSRF_HEADER_MISSING');
    const allowed: Readonly<Record<string, readonly string[]>> = {
      '/api/v1/health': ['GET'], '/api/v1/session': ['GET', 'POST', 'DELETE'], '/api/v1/entries': ['GET', 'POST'], '/api/v1/facets': ['GET'],
      '/api/v1/search': ['GET'], '/api/v1/retrieve': ['POST'], '/api/v1/queue': ['GET'], '/api/v1/batches/run': ['POST'], '/api/v1/export': ['POST'],
      '/api/v1/compilations': ['GET', 'POST'], '/api/v1/jobs': ['GET'],
    };
    const entryMatch = /^\/api\/v1\/entries\/([^/]+)(\/neighbors)?$/.exec(path);
    const jobMatch = /^\/api\/v1\/jobs\/([^/]+)\/retry$/.exec(path);
    const compilationMatch = /^\/api\/v1\/compilations\/([^/]+)$/.exec(path);
    const methods = allowed[path] ?? (entryMatch !== null ? (entryMatch[2] === undefined ? ['GET', 'PATCH', 'DELETE'] : ['GET'])
      : jobMatch !== null ? ['POST'] : compilationMatch !== null ? ['GET', 'DELETE'] : undefined);
    if (methods === undefined) throw new HttpProblem(404, 'Not Found', `No API route for ${path}. Check the /api/v1 endpoint path.`);
    if (!methods.includes(method)) throw new HttpProblem(405, 'Method Not Allowed', `${method} is not supported for ${path}. Use ${methods.join(', ')}.`, undefined, { Allow: methods.join(', ') });
    if (!session && !health) pipeline.schedule();
    if (health) return json({ status: 'ok' });
    if (session) {
      switch (method) {
        case 'POST': {
          checkSignInRateLimit();
          const payload = await body(request);
          checkSignInRateLimit();
          const token = payload['token'];
          if (typeof token !== 'string') invalid('token must be a string.');
          if (!token.trim() || token === 'wrong') {
            failures = [...failures, now()];
            throw new HttpProblem(401, 'Unauthorized', 'The supplied access token is invalid. Use the token printed by msj create-token.', 'BAD_TOKEN');
          }
          setSession(true);
          tokenRotated = false;
          return new Response(null, { status: 204 });
        }
        case 'DELETE': setSession(false); return new Response(null, { status: 204 });
        default: return new Response(null, { status: 204 });
      }
    }
    if (entryMatch !== null) {
      const id = decodeURIComponent(entryMatch[1] ?? '');
      const entry = store.entry(id);
      if (entryMatch[2] !== undefined) {
        const ordered = filtered(store, queryFilters(params));
        const index = ordered.findIndex((item) => item.id === id);
        return json({ newer: index < 0 ? null : ordered[index + 1]?.id ?? null, older: index < 0 ? null : ordered[index - 1]?.id ?? null });
      }
      switch (method) {
        case 'GET': return json(store.detail(entry, params.get('history') === '1' || params.get('history') === 'true'));
        case 'DELETE':
          store.deleteEntry(id);
          return new Response(null, { status: 204 });
        case 'PATCH': {
          const payload = await body(request);
          unknownFields(payload, ['title', 'tags', 'entry_date', 'text']);
          if (Object.keys(payload).length === 0) invalid('Provide at least one entry field');
          const newTitle = payload['title'] === undefined ? entry.title : title(payload['title']);
          const newTags = payload['tags'] === undefined ? entry.tags : strings(payload['tags'], 'Tags must be an array of strings');
          const date = payload['entry_date'] === undefined ? entry.entry_date : calendarDate(payload['entry_date']);
          const text = payload['text'];
          if ('text' in payload && (typeof text !== 'string' || !text.trim())) invalid('Text must contain non-whitespace characters');
          entry.title = newTitle; entry.tags = [...newTags]; entry.entry_date = date;
          if (typeof text === 'string') store.revise(entry, text);
          return json(store.detail(entry));
        }
      }
    }
    if (jobMatch !== null) {
      const id = decodeURIComponent(jobMatch[1] ?? '');
      const entry = [...store.entries.values()].find((item) => item.jobs.some((job) => job.id === id));
      const job = entry?.jobs.find((item) => item.id === id);
      if (entry === undefined || job === undefined) {
        throw new HttpProblem(404, 'Not Found', `Job not found: ${id}. Reload the entry's jobs.`, 'JOB_NOT_FOUND');
      }
      if (job.status !== 'failed' || job.revision !== entry.revision) throw new HttpProblem(409, 'Conflict', 'Only failed jobs for the current entry revision can be retried');
      job.status = 'queued'; job.attempts = 0; job.last_error = null; job.finished_at = null; job.not_before = now() / 1000;
      return json(store.jobView(entry, job));
    }
    if (compilationMatch !== null) {
      const id = decodeURIComponent(compilationMatch[1] ?? '');
      const compilation = store.compilations.get(id);
      if (compilation === undefined) {
        throw new HttpProblem(404, 'Not Found', `Compilation not found: ${id}. Reload the compilation list.`, 'COMPILATION_NOT_FOUND');
      }
      if (method === 'DELETE') { void store.compilations.delete(id); return new Response(null, { status: 204 }); }
      return json(compilationView(compilation));
    }
    switch (path) {
      case '/api/v1/entries': {
        if (method === 'GET') {
          const limit = queryInteger(params, 'limit', 100, 1, 500);
          const offset = queryInteger(params, 'offset', 0, 0, Number.MAX_SAFE_INTEGER);
          const view = params.get('view') ?? 'summary';
          const order = params.get('order') ?? 'desc';
          if (view !== 'summary' && view !== 'full') invalid('view must be summary or full.');
          if (order !== 'asc' && order !== 'desc') invalid('order must be asc or desc.');
          const entries = [...filtered(store, queryFilters(params))];
          if (order === 'desc') entries.reverse();
          return json({ items: entries.slice(offset, offset + limit).map((entry) => view === 'summary' ? entry : store.detail(store.entry(entry.id))), total: entries.length });
        }
        const contentType = request.headers.get('Content-Type')?.split(';')[0]?.toLowerCase();
        let entry: StoredEntry;
        if (contentType === 'application/json') {
          const payload = await body(request, true);
          unknownFields(payload, ['text', 'title', 'tags', 'entry_date']);
          const text = payload['text'];
          if (typeof text !== 'string' || !text.trim()) invalid('Provide non-empty text and valid entry fields.');
          entry = store.create('text', text, payload['title'] === undefined ? null : title(payload['title']), payload['tags'] === undefined ? [] : strings(payload['tags'], 'Tags must be an array of strings'),
            payload['entry_date'] === undefined || payload['entry_date'] === null ? localDate(now()) : calendarDate(payload['entry_date']));
        } else if (contentType === 'multipart/form-data') {
          let form: FormData;
          try { form = await request.formData(); }
          catch (cause) { if (request.signal.aborted) throw abortError(); throw new HttpProblem(422, 'Validation Error', `Could not parse multipart form data: ${cause instanceof Error ? cause.message.slice(0, 160) : 'invalid body'}. Include an audio file in field 'file'.`); }
          const file = form.get('file');
          if (file === null || typeof file === 'string') invalid("Include an audio file in the multipart field 'file'.");
          const extension = AUDIO_TYPES.get(file.type.toLowerCase());
          if (extension === undefined) throw new HttpProblem(415, 'Unsupported Media Type', 'Allowed audio types are WAV, MP3, MP4, WebM, Ogg, and FLAC.');
          if (file.size === 0) invalid('Audio file must not be empty.');
          if (file.size > 25 * 1024 * 1024) throw new HttpProblem(413, 'Payload Too Large', 'Audio exceeds the configured upload limit.');
          let tags: unknown;
          const tagsValue = form.get('tags') ?? '[]';
          if (typeof tagsValue !== 'string') invalid('Multipart tags must be a JSON array.');
          try { tags = JSON.parse(tagsValue); } catch { invalid('Multipart tags must be a JSON array.'); }
          const titleValue = form.get('title');
          const dateValue = form.get('entry_date');
          entry = store.create('audio', '', titleValue === null || titleValue === '' ? null : title(titleValue), strings(tags, 'Multipart tags must be a JSON array of strings.'),
            dateValue === null ? localDate(now()) : calendarDate(dateValue), store.timestamp(), extension);
        } else throw new HttpProblem(415, 'Unsupported Media Type', 'Use application/json for text entries or multipart/form-data for audio.');
        return json(store.detail(entry), 201);
      }
      case '/api/v1/facets': {
        const entries = [...store.entries.values()].map((entry) => store.summary(entry));
        return json({ tags: facet(entries.map((entry) => entry.tags)), projects: facet(entries.map((entry) => entry.projects)), skills: facet(entries.map((entry) => entry.skills)) });
      }
      case '/api/v1/search': return search(searchDocuments(store, queryFilters(params)), query(params.get('q'), 'q'), tiers(params), request.signal);
      case '/api/v1/retrieve': {
        const payload = await body(request);
        const q = query(payload['query'], 'query');
        const k = payload['k'] === undefined ? 20 : integer(payload['k'], 1, 100, 'k must be an integer between 1 and 100.');
        return json(retrieve(searchDocuments(store, bodyFilters(payload['filters'])), q, options.concepts, embeddings, k, now()));
      }
      case '/api/v1/queue': return json(pipeline.snapshot());
      case '/api/v1/batches/run':
        if (pipeline.running || pipeline.pending) {
          throw new HttpProblem(
            409,
            'Conflict',
            'A batch is already running or requested. Wait for the worker to finish it before requesting another.',
            'BATCH_ALREADY_RUNNING',
          );
        }
        pipeline.request();
        return json({ requested: true, ...(pipeline.stale ? { worker: { stale: true } } : {}) }, 202);
      case '/api/v1/jobs': {
        const status = params.get('status');
        if (status !== null && !['queued', 'running', 'done', 'failed', 'superseded'].includes(status)) invalid('Unknown job status');
        return json([...store.entries.values()].flatMap((entry) => entry.jobs.filter((job) => status === null || job.status === status).map((job) => store.jobView(entry, job))).sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)));
      }
      case '/api/v1/export': {
        const payload = await body(request);
        const entryIds = ids(store, payload['entry_ids'], 1000);
        const format = payload['format'];
        const order = payload['order'] ?? 'asc';
        if (format !== 'text' && format !== 'markdown' && format !== 'json') invalid('format must be text, markdown, or json.');
        if (order !== 'asc' && order !== 'desc') invalid('order must be asc or desc.');
        const fields = record(payload['include'], 'include must specify title, date, tags, summary, text, and extraction as booleans.');
        const include: Record<keyof ExportInclude, boolean> = { title: true, date: true, tags: true, summary: true, text: true, extraction: false };
        for (const field of ['title', 'date', 'tags', 'summary', 'text', 'extraction'] as const) {
          if (typeof fields[field] !== 'boolean') invalid(`include.${field} must be a boolean.`);
          include[field] = fields[field];
        }
        const exportRequest: ExportRequest = { entry_ids: entryIds, format, include, order };
        return exportResponse(entryIds.map((id) => store.detail(store.entry(id))), exportRequest, localDate(now()));
      }
      case '/api/v1/compilations': {
        if (method === 'GET') return json([...store.compilations.values()].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id)).map(compilationView));
        const payload = await body(request);
        const entryIds = ids(store, payload['entry_ids']);
        const purpose = payload['purpose'];
        const period = payload['period'];
        if (typeof purpose !== 'string' || typeof period !== 'string') invalid('purpose and period must be strings.');
        return json(compilationView(store.compile(entryIds, purpose, period)), 202);
      }
      default: assert(false, `recognized API path ${path} has a handler`); throw new Error(`Missing mock handler for ${path}`);
    }
  };
  const fetch: FetchLike = async (input, init) => {
    if (init.signal?.aborted === true) throw abortError();
    if ((options.latencyMs ?? 0) > 0) await tasks.delay(options.latencyMs ?? 0, init.signal);
    try { return await route(new Request(new URL(input, 'http://mock.invalid'), init)); }
    catch (error) { if (error instanceof HttpProblem) return error.response(); throw error; }
  };
  return { fetch, idle: () => tasks.idle() };
}
