import { assert } from '../lib/assert';

/**
 * Error model shown to the user: a plain message, a stable uppercase code, an
 * optional next step, and labelled details for the "Show details" expander.
 */

export interface DetailLine {
  readonly label: string;
  readonly value: string;
}

export interface AppErrorInit {
  readonly code: string;
  readonly message: string;
  readonly hint?: string | null;
  readonly details?: readonly DetailLine[];
  readonly cause?: unknown;
}

export class AppError extends Error {
  override readonly name: string = 'AppError';
  readonly code: string;
  readonly hint: string | null;
  readonly details: readonly DetailLine[];

  constructor(init: AppErrorInit) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    assert(/^[A-Z][A-Z0-9_]*$/.test(init.code), `error code must be UPPER_SNAKE: ${init.code}`);
    this.code = init.code;
    this.hint = init.hint ?? null;
    this.details = init.details ?? [];
  }
}

export type ApiErrorKind = 'http' | 'network' | 'response';

export interface ApiErrorInit extends AppErrorInit {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly method: string;
  readonly path: string;
  readonly retryAfterS: number | null;
}

export class ApiError extends AppError {
  override readonly name = 'ApiError';
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly method: string;
  readonly path: string;
  readonly retryAfterS: number | null;

  constructor(init: ApiErrorInit) {
    super(init);
    this.kind = init.kind;
    this.status = init.status;
    this.method = init.method;
    this.path = init.path;
    this.retryAfterS = init.retryAfterS;
  }
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

/** Codes for responses that carry no `code` of their own. */
export function codeForStatus(status: number): string {
  switch (status) {
    case 400:
      return 'BAD_REQUEST';
    case 401:
      return 'UNAUTHORIZED';
    case 403:
      return 'FORBIDDEN';
    case 404:
      return 'NOT_FOUND';
    case 409:
      return 'CONFLICT';
    case 413:
      return 'PAYLOAD_TOO_LARGE';
    case 415:
      return 'UNSUPPORTED_MEDIA_TYPE';
    case 422:
      return 'VALIDATION_ERROR';
    case 429:
      return 'RATE_LIMITED';
    case 502:
    case 503:
    case 504:
      return 'SERVER_UNAVAILABLE';
    default:
      return status >= 500 ? 'SERVER_ERROR' : `HTTP_${status}`;
  }
}

const HINTS: Readonly<Record<string, string>> = {
  BAD_TOKEN: 'Paste the token printed by `msj create-token`. Creating a new token replaces the old one.',
  UNAUTHORIZED: 'Sign in again with the access token.',
  TOKEN_REISSUE_REQUIRED:
    'The access token on the server was replaced. Create a new one on the server with `msj create-token`, then sign in with it.',
  INVALID_QUERY: 'Remove control characters (such as tabs or line breaks) from the search text.',
  BATCH_FAILED: 'Check the worker log on the server for the batch error, then run the batch again.',
  BATCH_LEASE_LOST: 'Another worker took over the batch lease; check that only one worker runs, then run the batch again.',
  WORKER_NOT_RUNNING: 'Start the worker on the server with `msj worker`; it then picks up the request.',
  CSRF_HEADER_MISSING: 'Reload the page. If it persists, the page and server versions differ.',
  BATCH_ALREADY_RUNNING: 'Wait for the running batch to finish; the queue updates on its own.',
  ENTRY_NOT_FOUND: 'The entry was deleted or never existed. Reload the list.',
  NOT_FOUND: 'It may have been deleted. Reload the list.',
  CONFLICT: 'Reload to see the current state, then try again.',
  PAYLOAD_TOO_LARGE: 'Send a shorter recording, or split it into several entries.',
  UNSUPPORTED_MEDIA_TYPE: 'Use WAV, MP3, MP4, WebM, Ogg or FLAC audio.',
  VALIDATION_ERROR: 'Correct the fields listed in the details, then try again.',
  NETWORK_UNREACHABLE: 'Check the connection and that the journal server is running, then try again.',
  SERVER_UNAVAILABLE: 'The server or its proxy is down or restarting. Try again in a minute.',
  SERVER_ERROR: 'Check the service log on the server for this request.',
  RESPONSE_INVALID: 'This page and the server disagree on the API shape. Deploy matching versions.',
  EMBEDDINGS_DISABLED: 'Set [embedding] provider to "sentence-transformers" in the server config to enable it.',
  EMBEDDING_MODEL_UNAVAILABLE: 'Check [embedding] model in the server config and the service log.',
  EMBEDDINGS_PENDING: 'Run the batch to embed the remaining entries.',
};

export function hintForCode(code: string, retryAfterS: number | null): string | null {
  if (code === 'RATE_LIMITED') {
    return retryAfterS === null
      ? 'Too many attempts. Wait a minute before trying again.'
      : `Too many attempts. Wait ${retryAfterS} s before trying again.`;
  }
  return HINTS[code] ?? null;
}

/** Bounds a value shown in the details pane. */
export function bounded(text: string, limit = 2000): string {
  return text.length > limit ? `${text.slice(0, limit)}\n... (${text.length - limit} more characters)` : text;
}

/** Turns anything thrown into an AppError for display. */
export function toAppError(error: unknown, operation: string): AppError {
  if (error instanceof AppError) return error;
  const message = error instanceof Error ? error.message : String(error);
  return new AppError({
    code: 'UNEXPECTED',
    message: `${operation} failed unexpectedly.`,
    hint: 'Reload the page. If it happens again, the details below say where.',
    details: [{ label: 'Cause', value: bounded(message) }],
    cause: error,
  });
}
