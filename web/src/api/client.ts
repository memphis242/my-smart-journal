/**
 * Typed client for the journal API v1. Every response is validated at this
 * boundary; failures surface as `ApiError` with a stable code.
 */

import { assert } from '../lib/assert';
import { ApiError, bounded, codeForStatus, hintForCode, isAbort, type DetailLine } from './errors';
import {
  batchRequested,
  compilation,
  compilations,
  entryDetail,
  entryPage,
  facets,
  neighbors,
  queueSnapshot,
  retriedJob,
  retrieveResult,
  searchEvent,
  ShapeError,
  type Guard,
} from './guards';
import { NdjsonLineError, readNdjson } from './ndjson';
import type {
  BatchRequested,
  Compilation,
  CompileRequest,
  EntryDetail,
  EntryFilters,
  EntryPage,
  EntryPatch,
  ExportRequest,
  ExportResult,
  Facets,
  Job,
  Neighbors,
  NewAudioEntry,
  NewTextEntry,
  QueueSnapshot,
  RetrieveResult,
  SearchEvent,
  SearchTier,
  SortOrder,
} from './types';

export const API_BASE = '/api/v1';

export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

export interface ClientOptions {
  readonly fetch: FetchLike;
  /** Called when an authenticated request is answered with 401. */
  readonly onUnauthorized: (error: ApiError) => void;
}

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';
type QueryValue = string | number | readonly string[] | undefined;
type Query = Readonly<Record<string, QueryValue>>;

interface RequestSpec {
  /** Names the operation in error messages, for example "Load entries". */
  readonly operation: string;
  readonly method: Method;
  readonly path: string;
  readonly query?: Query;
  readonly json?: unknown;
  readonly form?: FormData;
  readonly signal?: AbortSignal | undefined;
  /** 401 is an expected answer (sign-in), not a lost session. */
  readonly authProbe?: boolean;
}

export function buildQuery(query: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined) continue;
    if (typeof value === 'string' || typeof value === 'number') {
      params.append(key, String(value));
    } else {
      for (const item of value) params.append(key, item);
    }
  }
  const text = params.toString();
  return text === '' ? '' : `?${text}`;
}

export function filterQuery(filters: EntryFilters): Query {
  return {
    from: filters.from,
    to: filters.to,
    tag: filters.tag !== undefined && filters.tag.length > 0 ? filters.tag : undefined,
    project: filters.project !== undefined && filters.project.length > 0 ? filters.project : undefined,
    skill: filters.skill !== undefined && filters.skill.length > 0 ? filters.skill : undefined,
  };
}

/** Filters in the JSON body form used by POST /retrieve. */
function filterBody(filters: EntryFilters): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (filters.from !== undefined) body['from'] = filters.from;
  if (filters.to !== undefined) body['to'] = filters.to;
  if (filters.tag !== undefined && filters.tag.length > 0) body['tag'] = filters.tag;
  if (filters.project !== undefined && filters.project.length > 0) body['project'] = filters.project;
  if (filters.skill !== undefined && filters.skill.length > 0) body['skill'] = filters.skill;
  return body;
}

interface Problem {
  readonly title: string | null;
  readonly detail: string | null;
  readonly code: string | null;
  readonly type: string | null;
  readonly fieldErrors: readonly string[];
  readonly body: Readonly<Record<string, unknown>>;
}

function readProblem(body: string): Problem | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null;
  const record = parsed as Readonly<Record<string, unknown>>;
  const text = (key: string): string | null => {
    const value = record[key];
    return typeof value === 'string' && value !== '' ? value : null;
  };
  const errors = Array.isArray(record['errors']) ? (record['errors'] as readonly unknown[]) : [];
  // Validation issues name the field and the failed check (`type`); some servers add a `message`.
  const fieldErrors = errors.flatMap((item): string[] => {
    if (typeof item !== 'object' || item === null) return [];
    const issue = item as Readonly<Record<string, unknown>>;
    const field = typeof issue['field'] === 'string' ? issue['field'] : '(unnamed field)';
    const type = typeof issue['type'] === 'string' ? issue['type'] : null;
    const message = typeof issue['message'] === 'string' ? issue['message'] : null;
    const reason = type !== null && message !== null ? `${type} (${message})` : (type ?? message ?? 'invalid');
    return [`${field}: ${reason}`];
  });
  return { title: text('title'), detail: text('detail'), code: text('code'), type: text('type'), fieldErrors, body: record };
}

function retryAfterSeconds(response: Response): number | null {
  const header = response.headers.get('Retry-After');
  if (header === null) return null;
  const seconds = Number(header);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds);
  const at = Date.parse(header);
  return Number.isNaN(at) ? null : Math.max(0, Math.ceil((at - Date.now()) / 1000));
}

const FILENAME_STAR = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/;
const FILENAME_PLAIN = /filename\s*=\s*"?([^";]+)"?/;

export function attachmentName(disposition: string | null, fallback: string): string {
  if (disposition === null) return fallback;
  const star = FILENAME_STAR.exec(disposition);
  if (star?.[1] !== undefined) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      return fallback;
    }
  }
  const plain = FILENAME_PLAIN.exec(disposition);
  return plain?.[1] !== undefined ? plain[1].trim() : fallback;
}

const EXPORT_EXTENSIONS = { text: 'txt', markdown: 'md', json: 'json' } as const;

export class ApiClient {
  readonly #fetch: FetchLike;
  readonly #onUnauthorized: (error: ApiError) => void;

  constructor(options: ClientOptions) {
    this.#fetch = options.fetch;
    this.#onUnauthorized = options.onUnauthorized;
  }

  async #send(spec: RequestSpec): Promise<Response> {
    assert(spec.json === undefined || spec.form === undefined, 'a request has one body');
    const url = `${API_BASE}${spec.path}${spec.query === undefined ? '' : buildQuery(spec.query)}`;
    const headers = new Headers({ Accept: 'application/json, application/x-ndjson;q=0.9, */*;q=0.1' });
    if (spec.method !== 'GET') headers.set('X-MSJ-CSRF', '1');
    let body: BodyInit | null = null;
    if (spec.json !== undefined) {
      headers.set('Content-Type', 'application/json');
      body = JSON.stringify(spec.json);
    } else if (spec.form !== undefined) {
      body = spec.form;
    }
    const init: RequestInit = { method: spec.method, headers, body, credentials: 'same-origin', cache: 'no-store' };
    if (spec.signal !== undefined) init.signal = spec.signal;

    let response: Response;
    try {
      response = await this.#fetch(url, init);
    } catch (error) {
      if (isAbort(error)) throw error;
      throw new ApiError({
        kind: 'network',
        code: 'NETWORK_UNREACHABLE',
        status: null,
        method: spec.method,
        path: spec.path,
        retryAfterS: null,
        message: `${spec.operation} failed: the server could not be reached.`,
        hint: hintForCode('NETWORK_UNREACHABLE', null),
        details: [
          { label: 'Request', value: `${spec.method} ${API_BASE}${spec.path}` },
          { label: 'Cause', value: error instanceof Error ? error.message : String(error) },
        ],
        cause: error,
      });
    }
    if (response.ok) return response;
    const error = await this.#httpError(spec, response);
    if (response.status === 401 && spec.authProbe !== true) this.#onUnauthorized(error);
    throw error;
  }

  async #httpError(spec: RequestSpec, response: Response): Promise<ApiError> {
    const text = await response.text().catch((error: unknown) => `(body unreadable: ${String(error)})`);
    const problem = readProblem(text);
    const code = problem?.code ?? codeForStatus(response.status);
    const retryAfterS = retryAfterSeconds(response);
    const reason = problem?.detail ?? problem?.title ?? `HTTP ${response.status} ${response.statusText}`.trim();
    const details: DetailLine[] = [
      { label: 'Request', value: `${spec.method} ${API_BASE}${spec.path}` },
      { label: 'Status', value: `${response.status} ${response.statusText}`.trim() },
    ];
    if (problem?.title != null && problem.detail != null) details.push({ label: 'Title', value: problem.title });
    if (problem?.type != null && problem.type !== 'about:blank') details.push({ label: 'Type', value: problem.type });
    if (problem !== null && problem.fieldErrors.length > 0) {
      details.push({ label: 'Fields', value: problem.fieldErrors.join('\n') });
    }
    if (retryAfterS !== null) details.push({ label: 'Retry after', value: `${retryAfterS} s` });
    if (problem === null && text !== '') details.push({ label: 'Body', value: bounded(text, 1200) });
    return new ApiError({
      kind: 'http',
      code,
      status: response.status,
      method: spec.method,
      path: spec.path,
      retryAfterS,
      message: `${spec.operation} failed: ${reason}`,
      hint: hintForCode(code, retryAfterS),
      details,
      problem: problem?.body ?? null,
    });
  }

  #shapeError(spec: RequestSpec, error: unknown): ApiError {
    const details: DetailLine[] = [{ label: 'Request', value: `${spec.method} ${API_BASE}${spec.path}` }];
    if (error instanceof ShapeError) {
      details.push({ label: 'Field', value: error.path === '' ? '(top level)' : error.path });
      details.push({ label: 'Expected', value: error.expected });
      details.push({ label: 'Found', value: error.found });
    } else if (error instanceof NdjsonLineError) {
      details.push({ label: 'Stream line', value: String(error.line) });
      details.push({ label: 'Text', value: error.text });
    } else {
      details.push({ label: 'Cause', value: error instanceof Error ? error.message : String(error) });
    }
    return new ApiError({
      kind: 'response',
      code: 'RESPONSE_INVALID',
      status: null,
      method: spec.method,
      path: spec.path,
      retryAfterS: null,
      message: `${spec.operation} failed: the server's answer did not match the expected shape.`,
      hint: hintForCode('RESPONSE_INVALID', null),
      details,
      cause: error,
    });
  }

  async #json<T>(spec: RequestSpec, guard: Guard<T>): Promise<T> {
    const response = await this.#send(spec);
    let parsed: unknown;
    try {
      parsed = await response.json();
    } catch (error) {
      if (isAbort(error)) throw error;
      throw this.#shapeError(spec, error);
    }
    try {
      return guard(parsed, '');
    } catch (error) {
      throw this.#shapeError(spec, error);
    }
  }

  async #empty(spec: RequestSpec): Promise<void> {
    const response = await this.#send(spec);
    // The body of a 204 is empty; reading it releases the connection.
    await response.arrayBuffer();
  }

  // ---------------------------------------------------------------- session

  /**
   * GET /session: null when the browser holds a valid session, else the 401
   * error (its code says why, for example TOKEN_REISSUE_REQUIRED).
   */
  async sessionProblem(signal?: AbortSignal): Promise<ApiError | null> {
    try {
      await this.#empty({ operation: 'Check sign-in', method: 'GET', path: '/session', signal, authProbe: true });
      return null;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return error;
      throw error;
    }
  }

  signIn(token: string): Promise<void> {
    assert(token.trim() !== '', 'sign-in token is non-empty');
    return this.#empty({ operation: 'Sign in', method: 'POST', path: '/session', json: { token }, authProbe: true });
  }

  signOut(): Promise<void> {
    return this.#empty({ operation: 'Sign out', method: 'DELETE', path: '/session', authProbe: true });
  }

  // ---------------------------------------------------------------- entries

  listEntries(
    params: {
      readonly filters: EntryFilters;
      readonly order: SortOrder;
      readonly limit: number;
      readonly offset: number;
    },
    signal?: AbortSignal,
  ): Promise<EntryPage> {
    assert(params.limit >= 1 && params.limit <= 500, 'limit is 1-500');
    assert(params.offset >= 0, 'offset is non-negative');
    return this.#json(
      {
        operation: 'Load entries',
        method: 'GET',
        path: '/entries',
        query: { ...filterQuery(params.filters), view: 'summary', order: params.order, limit: params.limit, offset: params.offset },
        signal,
      },
      entryPage,
    );
  }

  getEntry(id: string, signal?: AbortSignal): Promise<EntryDetail> {
    return this.#json(
      { operation: 'Load entry', method: 'GET', path: `/entries/${encodeURIComponent(id)}`, signal },
      entryDetail,
    );
  }

  neighbors(id: string, filters: EntryFilters, signal?: AbortSignal): Promise<Neighbors> {
    return this.#json(
      {
        operation: 'Find the next entry',
        method: 'GET',
        path: `/entries/${encodeURIComponent(id)}/neighbors`,
        query: filterQuery(filters),
        signal,
      },
      neighbors,
    );
  }

  createTextEntry(entry: NewTextEntry): Promise<EntryDetail> {
    assert(entry.text.trim() !== '', 'entry text is non-empty');
    return this.#json(
      {
        operation: 'Add entry',
        method: 'POST',
        path: '/entries',
        json: { text: entry.text, title: entry.title, tags: entry.tags, entry_date: entry.entry_date },
      },
      entryDetail,
    );
  }

  createAudioEntry(entry: NewAudioEntry): Promise<EntryDetail> {
    assert(!entry.mimeType.includes(';'), 'audio MIME type is bare, without parameters');
    const form = new FormData();
    form.append('file', new File([entry.audio], entry.fileName, { type: entry.mimeType }));
    if (entry.title !== null) form.append('title', entry.title);
    form.append('entry_date', entry.entry_date);
    form.append('tags', JSON.stringify(entry.tags));
    return this.#json({ operation: 'Upload recording', method: 'POST', path: '/entries', form }, entryDetail);
  }

  patchEntry(id: string, patch: EntryPatch): Promise<EntryDetail> {
    assert(Object.keys(patch).length > 0, 'a patch changes at least one field');
    return this.#json(
      { operation: 'Save entry', method: 'PATCH', path: `/entries/${encodeURIComponent(id)}`, json: patch },
      entryDetail,
    );
  }

  deleteEntry(id: string): Promise<void> {
    return this.#empty({ operation: 'Delete entry', method: 'DELETE', path: `/entries/${encodeURIComponent(id)}` });
  }

  facets(signal?: AbortSignal): Promise<Facets> {
    return this.#json({ operation: 'Load filters', method: 'GET', path: '/facets', signal }, facets);
  }

  // ---------------------------------------------------------------- search

  /**
   * Streams the tiered search. Each tier event is passed to `onEvent` as soon as
   * its line arrives; the promise settles after the completion event.
   */
  async search(
    params: { readonly q: string; readonly filters: EntryFilters; readonly tiers: readonly SearchTier[] },
    onEvent: (event: SearchEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    assert(params.q.trim() !== '', 'search query is non-empty');
    assert(params.tiers.length > 0, 'at least one search tier');
    const spec: RequestSpec = {
      operation: 'Search',
      method: 'GET',
      path: '/search',
      query: { q: params.q, ...filterQuery(params.filters), tiers: params.tiers.join(',') },
      signal,
    };
    const response = await this.#send(spec);
    if (response.body === null) throw this.#shapeError(spec, new Error('the response has no body'));
    let sawDone = false;
    try {
      for await (const value of readNdjson(response.body)) {
        const event = searchEvent(value, '');
        onEvent(event);
        if (event.type === 'done') sawDone = true;
      }
    } catch (error) {
      if (isAbort(error)) throw error;
      if (error instanceof ShapeError || error instanceof NdjsonLineError) throw this.#shapeError(spec, error);
      throw new ApiError({
        kind: 'network',
        code: 'STREAM_INTERRUPTED',
        status: response.status,
        method: spec.method,
        path: spec.path,
        retryAfterS: null,
        message: 'Search failed: the result stream stopped before it finished.',
        hint: 'Search again. If it keeps stopping, check the proxy buffering and the service log.',
        details: [{ label: 'Cause', value: error instanceof Error ? error.message : String(error) }],
        cause: error,
      });
    }
    if (!sawDone) {
      throw new ApiError({
        kind: 'response',
        code: 'STREAM_INCOMPLETE',
        status: response.status,
        method: spec.method,
        path: spec.path,
        retryAfterS: null,
        message: 'Search failed: the result stream ended without its completion line.',
        hint: 'Search again. If it repeats, check the service log for this request.',
      });
    }
  }

  retrieve(
    params: { readonly query: string; readonly k: number; readonly filters: EntryFilters },
    signal?: AbortSignal,
  ): Promise<RetrieveResult> {
    assert(params.query.trim() !== '', 'retrieval query is non-empty');
    assert(params.k >= 1, 'k is positive');
    return this.#json(
      {
        operation: 'Find relevant entries',
        method: 'POST',
        path: '/retrieve',
        json: { query: params.query, k: params.k, filters: filterBody(params.filters) },
        signal,
      },
      retrieveResult,
    );
  }

  // ---------------------------------------------------------------- queue

  queue(signal?: AbortSignal): Promise<QueueSnapshot> {
    return this.queueStatus(signal).then((status) => status.snapshot);
  }

  /**
   * GET /queue with the server's clock from the Date header (null when absent),
   * so liveness can be judged without trusting the browser's clock.
   */
  async queueStatus(signal?: AbortSignal): Promise<{ readonly snapshot: QueueSnapshot; readonly serverTimeMs: number | null }> {
    const spec: RequestSpec = { operation: 'Load the queue', method: 'GET', path: '/queue', signal };
    const response = await this.#send(spec);
    const header = response.headers.get('Date');
    const parsedDate = header === null ? Number.NaN : Date.parse(header);
    let parsed: unknown;
    try {
      parsed = await response.json();
    } catch (error) {
      if (isAbort(error)) throw error;
      throw this.#shapeError(spec, error);
    }
    try {
      return { snapshot: queueSnapshot(parsed, ''), serverTimeMs: Number.isNaN(parsedDate) ? null : parsedDate };
    } catch (error) {
      throw this.#shapeError(spec, error);
    }
  }

  /** Asks the worker to run a batch; it starts within a few seconds when the worker is alive. */
  runBatch(): Promise<BatchRequested> {
    return this.#json({ operation: 'Run batch', method: 'POST', path: '/batches/run' }, batchRequested);
  }

  retryJob(jobId: string): Promise<Job> {
    return this.#json(
      { operation: 'Retry job', method: 'POST', path: `/jobs/${encodeURIComponent(jobId)}/retry` },
      retriedJob,
    );
  }

  // ---------------------------------------------------------------- export

  async exportEntries(request: ExportRequest, signal?: AbortSignal): Promise<ExportResult> {
    assert(request.entry_ids.length > 0, 'export has at least one entry');
    const spec: RequestSpec = { operation: 'Export', method: 'POST', path: '/export', json: request, signal };
    const response = await this.#send(spec);
    const body = await response.text();
    const today = new Date().toISOString().slice(0, 10);
    const fallback = `journal-export-${today}.${EXPORT_EXTENSIONS[request.format]}`;
    return {
      body,
      contentType: response.headers.get('Content-Type') ?? 'text/plain; charset=utf-8',
      filename: attachmentName(response.headers.get('Content-Disposition'), fallback),
    };
  }

  // ---------------------------------------------------------------- compilations

  createCompilation(request: CompileRequest): Promise<Compilation> {
    assert(request.entry_ids.length > 0, 'compilation has at least one entry');
    return this.#json(
      { operation: 'Queue compilation', method: 'POST', path: '/compilations', json: request },
      compilation,
    );
  }

  listCompilations(signal?: AbortSignal): Promise<readonly Compilation[]> {
    return this.#json(
      { operation: 'Load compilations', method: 'GET', path: '/compilations', signal },
      compilations,
    );
  }

  getCompilation(id: string, signal?: AbortSignal): Promise<Compilation> {
    return this.#json(
      { operation: 'Load compilation', method: 'GET', path: `/compilations/${encodeURIComponent(id)}`, signal },
      compilation,
    );
  }

  deleteCompilation(id: string): Promise<void> {
    return this.#empty({
      operation: 'Delete compilation',
      method: 'DELETE',
      path: `/compilations/${encodeURIComponent(id)}`,
    });
  }
}
