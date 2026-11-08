import { assert } from '../lib/assert';

const PROBLEM_CODES: ReadonlyMap<number, string> = new Map([
  [400, 'BAD_REQUEST'],
  [401, 'UNAUTHORIZED'],
  [403, 'FORBIDDEN'],
  [404, 'NOT_FOUND'],
  [405, 'METHOD_NOT_ALLOWED'],
  [409, 'CONFLICT'],
  [413, 'PAYLOAD_TOO_LARGE'],
  [415, 'UNSUPPORTED_MEDIA_TYPE'],
  [422, 'VALIDATION_ERROR'],
  [429, 'RATE_LIMITED'],
  [500, 'INTERNAL_SERVER_ERROR'],
]);

/** HTTP boundary utilities shared by the in-memory API. */
export class HttpProblem extends Error {
  readonly status: number;
  readonly title: string;
  readonly code: string;
  readonly headers: HeadersInit;

  constructor(
    status: number,
    title: string,
    detail: string,
    code?: string,
    headers: HeadersInit = {},
  ) {
    super(detail);
    this.status = status;
    this.title = title;
    const problemCode = code ?? PROBLEM_CODES.get(status);
    assert(problemCode !== undefined, `problem status ${status} has a stable code`);
    assert(/^[A-Z][A-Z0-9_]*$/.test(problemCode), 'problem code is uppercase snake case');
    this.code = problemCode;
    this.headers = headers;
  }

  response(): Response {
    return json(
      {
        type: 'about:blank',
        title: this.title,
        status: this.status,
        detail: this.message,
        code: this.code,
      },
      this.status,
      {
        ...Object.fromEntries(new Headers(this.headers)),
        'Content-Type': 'application/problem+json',
      },
    );
  }
}

export function json(value: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json', ...Object.fromEntries(new Headers(headers)) } });
}

export function invalid(detail: string): never {
  throw new HttpProblem(422, 'Validation Error', detail);
}

export function record(value: unknown, detail = 'Request body must be a JSON object.'): Readonly<Record<string, unknown>> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) invalid(detail);
  return value as Readonly<Record<string, unknown>>;
}

export function strings(value: unknown, detail: string): readonly string[] {
  if (!Array.isArray(value) || !value.every((item: unknown) => typeof item === 'string')) invalid(detail);
  return value;
}

export function calendarDate(value: unknown): string {
  if (typeof value !== 'string') invalid('entry_date must be an ISO calendar date.');
  // Python date.fromisoformat also accepts compact dates and ISO week dates.
  const compact = /^(\d{4})(\d{2})(\d{2})$/.exec(value);
  const normalized = compact === null ? value : `${compact[1] ?? ''}-${compact[2] ?? ''}-${compact[3] ?? ''}`;
  const week = /^(\d{4})-?W(\d{2})(?:-?([1-7]))?$/.exec(normalized);
  if (week !== null) {
    const year = Number(week[1]);
    const number = Number(week[2]);
    const day = Number(week[3] ?? 1);
    if (year < 1 || year > 9999) invalid('entry_date must be an ISO calendar date.');
    const january = new Date(0);
    january.setUTCFullYear(year, 0, 4);
    january.setUTCHours(0, 0, 0, 0);
    const monday = january.getTime() - ((january.getUTCDay() + 6) % 7) * 86400000;
    const result = new Date(monday + ((number - 1) * 7 + day - 1) * 86400000);
    const thursday = new Date(result);
    thursday.setUTCDate(result.getUTCDate() + 4 - (result.getUTCDay() || 7));
    if (number < 1 || number > 53 || thursday.getUTCFullYear() !== year || result.getUTCFullYear() > 9999) invalid('entry_date must be an ISO calendar date.');
    return result.toISOString().slice(0, 10);
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(normalized) || normalized.startsWith('0000')) invalid('entry_date must be an ISO calendar date.');
  const parsed = new Date(`${normalized}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== normalized) invalid('entry_date must be an ISO calendar date.');
  return normalized;
}

export function localDate(epoch: number): string {
  const date = new Date(epoch);
  return `${String(date.getFullYear()).padStart(4, '0')}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function abortError(): DOMException {
  return new DOMException('The mock API request was aborted.', 'AbortError');
}

/** Tracks only finite work, so idle() does not wait for a worker polling interval. */
export class Tasks {
  readonly #pending = new Set<Promise<void>>();
  #failure: unknown = undefined;

  track(task: Promise<void>): void {
    const completion = task.catch((error: unknown) => { this.#failure = error; }).finally(() => {
      // The task has completed, so removing its tracked promise releases idle().
      void this.#pending.delete(completion);
    });
    void this.#pending.add(completion); // Set insertion has no result needed by the caller.
  }

  async idle(): Promise<void> {
    while (this.#pending.size > 0) await Promise.all(this.#pending);
    if (this.#failure !== undefined) throw this.#failure instanceof Error ? this.#failure : new Error('Mock background work failed. Inspect the cause and retry the mock operation.', { cause: this.#failure });
  }

  delay(ms: number, signal?: AbortSignal | null): Promise<void> {
    if (signal?.aborted === true) return Promise.reject(abortError());
    const task = new Promise<void>((resolve, reject) => {
      const abort = (): void => {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        reject(abortError());
      };
      const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, Math.max(0, ms));
      signal?.addEventListener('abort', abort, { once: true });
    });
    // Request cancellation is delivered to its consumer, rather than treated as a background failure.
    this.track(task.catch((error: unknown) => { if (!(error instanceof DOMException && error.name === 'AbortError')) throw error; }));
    return task;
  }
}
