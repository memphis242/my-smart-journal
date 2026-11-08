/**
 * Runtime validation of API responses.
 *
 * Each guard checks an untrusted JSON value and returns a fresh, typed value that
 * holds only the fields the UI relies on. A mismatch throws `ShapeError` naming
 * the JSON path, the expected shape and the value that was found, so a contract
 * drift between server and client reads as one precise message.
 */

import type {
  Artifact,
  BatchRequested,
  BlockedBy,
  Compilation,
  CompileResult,
  EntryDetail,
  EntryPage,
  EntrySummary,
  Extraction,
  FacetValue,
  Facets,
  Job,
  Neighbors,
  QueueItem,
  QueueSnapshot,
  RetrieveItem,
  RetrieveResult,
  ReviewSession,
  ReviewTarget,
  SearchEvent,
  SearchHit,
  SearchTier,
  Unavailable,
} from './types';
import {
  COMPILATION_STATUSES,
  ENTRY_SOURCES,
  ENTRY_STATUSES,
  JOB_STATUSES,
  QUEUE_ITEM_STATUSES,
  REVIEW_SESSION_STATUSES,
  REVIEW_TARGETS,
  SEARCH_TIERS,
} from './types';

export class ShapeError extends Error {
  override readonly name = 'ShapeError';
  readonly path: string;
  readonly expected: string;
  readonly found: string;

  constructor(path: string, expected: string, value: unknown) {
    const found = describe(value);
    super(`${path === '' ? 'response' : path}: expected ${expected}, found ${found}`);
    this.path = path;
    this.expected = expected;
    this.found = found;
  }
}

function describe(value: unknown): string {
  if (value === null) return 'null';
  if (value === undefined) return 'nothing (field missing)';
  if (Array.isArray(value)) return `array(${value.length})`;
  if (typeof value === 'string') {
    const shown = value.length > 40 ? `${value.slice(0, 40)}...` : value;
    return `string ${JSON.stringify(shown)}`;
  }
  if (typeof value === 'number' || typeof value === 'boolean') return `${typeof value} ${String(value)}`;
  return typeof value;
}

export type Guard<T> = (value: unknown, path: string) => T;
export type Infer<G> = G extends Guard<infer T> ? T : never;

function join(path: string, key: string | number): string {
  return typeof key === 'number' ? `${path}[${key}]` : path === '' ? key : `${path}.${key}`;
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export const str: Guard<string> = (value, path) => {
  if (typeof value !== 'string') throw new ShapeError(path, 'string', value);
  return value;
};

export const num: Guard<number> = (value, path) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new ShapeError(path, 'finite number', value);
  return value;
};

export const int: Guard<number> = (value, path) => {
  if (typeof value !== 'number' || !Number.isInteger(value)) throw new ShapeError(path, 'integer', value);
  return value;
};

export const bool: Guard<boolean> = (value, path) => {
  if (typeof value !== 'boolean') throw new ShapeError(path, 'boolean', value);
  return value;
};

/** Accepts SQLite-style 0/1 as well as true/false. */
export const flag: Guard<boolean> = (value, path) => {
  if (typeof value === 'boolean') return value;
  if (value === 0 || value === 1) return value === 1;
  throw new ShapeError(path, 'boolean', value);
};

export function oneOf<const T extends readonly (string | number)[]>(values: T): Guard<T[number]> {
  const allowed = new Set<unknown>(values);
  const expected = `one of ${values.map((v) => JSON.stringify(v)).join(' | ')}`;
  return (value, path) => {
    if (!allowed.has(value)) throw new ShapeError(path, expected, value);
    return value as T[number];
  };
}

export function nullable<T>(guard: Guard<T>): Guard<T | null> {
  return (value, path) => (value === null ? null : guard(value, path));
}

/** A field that may be missing or null; both read as null. */
export function nullish<T>(guard: Guard<T>): Guard<T | null> {
  return (value, path) => (value === null || value === undefined ? null : guard(value, path));
}

/** A field that may be missing; a missing value reads as `fallback`. */
export function withDefault<T>(guard: Guard<T>, fallback: T): Guard<T> {
  return (value, path) => (value === undefined ? fallback : guard(value, path));
}

export function arrayOf<T>(guard: Guard<T>): Guard<readonly T[]> {
  return (value, path) => {
    if (!Array.isArray(value)) throw new ShapeError(path, 'array', value);
    return value.map((item: unknown, index) => guard(item, join(path, index)));
  };
}

type Shape = Readonly<Record<string, Guard<unknown>>>;
type FromShape<S extends Shape> = { readonly [K in keyof S]: Infer<S[K]> };

export function object<S extends Shape>(shape: S): Guard<FromShape<S>> {
  const keys = Object.keys(shape);
  return (value, path) => {
    if (!isRecord(value)) throw new ShapeError(path, 'object', value);
    const out: Record<string, unknown> = {};
    for (const key of keys) {
      const guard = shape[key];
      if (guard === undefined) throw new Error(`internal: guard for ${key} is missing`);
      out[key] = guard(value[key], join(path, key));
    }
    return out as FromShape<S>;
  };
}

/** Maps an ISO timestamp or epoch seconds (SQLite REAL) to an ISO timestamp. */
export const timestamp: Guard<string> = (value, path) => {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return new Date(value * 1000).toISOString();
  throw new ShapeError(path, 'ISO timestamp or epoch seconds', value);
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
export const isoDate: Guard<string> = (value, path) => {
  if (typeof value !== 'string' || !ISO_DATE.test(value)) throw new ShapeError(path, 'date YYYY-MM-DD', value);
  return value;
};

const strings = arrayOf(str);

// ---------------------------------------------------------------------------
// Compile-time checks that each guard yields exactly the declared API type.

// The identity comparison needs the otherwise-unused generic parameter.
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters
type Equals<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
type Assert<T extends true> = T;

// ---------------------------------------------------------------------------
// Entries

const entrySummaryShape = {
  id: str,
  entry_date: isoDate,
  created_at: str,
  title: nullish(str),
  tags: strings,
  source: oneOf(ENTRY_SOURCES),
  summary: nullish(str),
  text: str,
  projects: withDefault(strings, []),
  skills: withDefault(strings, []),
  status: oneOf(ENTRY_STATUSES),
  revision: int,
} as const;

export const entrySummary: Guard<EntrySummary> = object(entrySummaryShape);

export const entryPage: Guard<EntryPage> = object({ items: arrayOf(entrySummary), total: int });

const artifact = object({
  id: str,
  kind: str,
  content: str,
  model: nullish(str),
  created_at: str,
  revision: int,
  needs_attention: withDefault(flag, false),
  target: nullish(str),
  turn: nullish(int),
});

const blockedBy = object({ job_id: str, kind: str, message: str });

const job = object({
  id: str,
  kind: str,
  status: oneOf(JOB_STATUSES),
  attempts: withDefault(int, 0),
  last_error: nullish(str),
  created_at: str,
  finished_at: nullish(str),
  revision: int,
  target: nullish(str),
  turn: nullish(int),
  blocked_by: withDefault(arrayOf(blockedBy), []),
});

const reviewSession = object({
  status: oneOf(REVIEW_SESSION_STATUSES),
  turns_used: withDefault(int, 0),
  remaining_claims: withDefault(strings, []),
});

const reviewSessions: Guard<Readonly<Partial<Record<ReviewTarget, ReviewSession>>>> = (value, path) => {
  if (value === undefined || value === null) return {};
  if (!isRecord(value)) throw new ShapeError(path, 'object', value);
  const out: Partial<Record<ReviewTarget, ReviewSession>> = {};
  for (const target of REVIEW_TARGETS) {
    if (value[target] !== undefined) out[target] = reviewSession(value[target], join(path, target));
  }
  return out;
};

export const entryDetail: Guard<EntryDetail> = object({
  ...entrySummaryShape,
  needs_attention: withDefault(flag, false),
  compilation_count: withDefault(int, 0),
  artifacts: withDefault(arrayOf(artifact), []),
  jobs: withDefault(arrayOf(job), []),
  review_sessions: reviewSessions,
});

export const neighbors: Guard<Neighbors> = object({ newer: nullable(str), older: nullable(str) });

const facetValue = object({ value: str, count: int });
export const facets: Guard<Facets> = object({
  tags: arrayOf(facetValue),
  projects: arrayOf(facetValue),
  skills: arrayOf(facetValue),
});

export const extraction: Guard<Extraction> = object({
  projects: withDefault(strings, []),
  skills_technologies: withDefault(strings, []),
  accomplishments: withDefault(arrayOf(object({ description: str, metrics: withDefault(strings, []) })), []),
  people_roles: withDefault(arrayOf(object({ name: str, role: str })), []),
  star_fragments: withDefault(arrayOf(object({ situation: str, task: str, action: str, result: str })), []),
  tags: withDefault(strings, []),
});

// ---------------------------------------------------------------------------
// Search and retrieval

const unavailable = object({ code: str, detail: str });

const searchHit = object({
  entry_id: str,
  score: num,
  snippet: nullish(str),
  passage: nullish(str),
  entry: nullish(entrySummary),
});

const tierNumber = oneOf(SEARCH_TIERS);

/** One NDJSON line of `GET /search`. */
export const searchEvent: Guard<SearchEvent> = (value, path) => {
  if (!isRecord(value)) throw new ShapeError(path, 'object', value);
  if (value['done'] === true) {
    const error = nullish(object({ status: nullish(int), detail: str, code: nullish(str) }))(value['error'], join(path, 'error'));
    return { type: 'done', elapsed_ms: nullish(num)(value['elapsed_ms'], join(path, 'elapsed_ms')), error };
  }
  return {
    type: 'tier',
    tier: tierNumber(value['tier'], join(path, 'tier')),
    name: str(value['name'], join(path, 'name')),
    results: arrayOf(searchHit)(value['results'], join(path, 'results')),
    elapsed_ms: withDefault(num, 0)(value['elapsed_ms'], join(path, 'elapsed_ms')),
    unavailable: nullish(unavailable)(value['unavailable'], join(path, 'unavailable')),
  };
};

const retrieveItem = object({ entry: entrySummary, score: num, passages: withDefault(strings, []) });
export const retrieveResult: Guard<RetrieveResult> = object({
  items: arrayOf(retrieveItem),
  unavailable: nullish(unavailable),
});

// ---------------------------------------------------------------------------
// Queue

const queueItem = object({
  entry_id: nullish(str),
  entry_title: nullish(str),
  entry_date: nullish(str),
  job_id: str,
  kind: str,
  target: nullish(str),
  turn: nullish(int),
  status: oneOf(QUEUE_ITEM_STATUSES),
  blocked_by: withDefault(arrayOf(blockedBy), []),
  last_error: nullish(str),
  not_before: nullish(timestamp),
});

export const queueSnapshot: Guard<QueueSnapshot> = object({
  batch: object({
    running: bool,
    started_at: nullish(str),
    trigger: nullish(oneOf(['manual', 'schedule'] as const)),
    last_finished_at: nullish(str),
    last_result: nullish(object({ processed: int, failed: int })),
    last_error: nullish(object({ code: str, detail: str })),
  }),
  worker: object({
    last_seen_at: nullish(str),
    interval_s: num,
    next_run_at: nullish(str),
  }),
  counts: object({ queued: int, running: int, failed: int, blocked: withDefault(int, 0) }),
  items: arrayOf(queueItem),
});

const isTrue: Guard<true> = (value, path) => {
  if (value !== true) throw new ShapeError(path, 'true', value);
  return true;
};

export const batchRequested: Guard<BatchRequested> = object({
  requested: isTrue,
  worker: nullish(object({ stale: bool })),
});

export const retriedJob: Guard<Job> = job;

// ---------------------------------------------------------------------------
// Compilations

const evidenceDates = withDefault(strings, []);

export const compileResult: Guard<CompileResult> = object({
  resume_bullets: withDefault(arrayOf(object({ text: str, evidence_dates: evidenceDates })), []),
  star_stories: withDefault(
    arrayOf(
      object({
        title: str,
        situation: str,
        task: str,
        action: str,
        result: str,
        evidence_dates: evidenceDates,
        competencies: withDefault(strings, []),
      }),
    ),
    [],
  ),
  technical_topics: withDefault(arrayOf(object({ topic: str, depth_notes: str, evidence_dates: evidenceDates })), []),
  open_threads: withDefault(strings, []),
});

export const compilation: Guard<Compilation> = object({
  id: str,
  created_at: str,
  status: oneOf(COMPILATION_STATUSES),
  entry_ids: strings,
  purpose: str,
  period: str,
  result: nullish(compileResult),
  last_error: nullish(str),
  model: nullish(str),
  prompt_sha256: nullish(str),
});

const compilationArray = arrayOf(compilation);

/** GET /compilations: a bare array, or `{items: [...]}` like GET /entries. */
export const compilations: Guard<readonly Compilation[]> = (value, path) =>
  isRecord(value) ? compilationArray(value['items'], join(path, 'items')) : compilationArray(value, path);

/** Compile-time proof that every guard produces exactly its declared type. */
export type GuardTypeChecks = [
  Assert<Equals<Infer<typeof artifact>, Artifact>>,
  Assert<Equals<Infer<typeof job>, Job>>,
  Assert<Equals<Infer<typeof blockedBy>, BlockedBy>>,
  Assert<Equals<Infer<typeof facetValue>, FacetValue>>,
  Assert<Equals<Infer<typeof searchHit>, SearchHit>>,
  Assert<Equals<Infer<typeof unavailable>, Unavailable>>,
  Assert<Equals<Infer<typeof retrieveItem>, RetrieveItem>>,
  Assert<Equals<Infer<typeof queueItem>, QueueItem>>,
  Assert<Equals<Infer<typeof reviewSession>, ReviewSession>>,
  Assert<Equals<Infer<typeof tierNumber>, SearchTier>>,
  Assert<Equals<FromShape<typeof entrySummaryShape>, EntrySummary>>,
];
