/** Polls GET /queue; faster while a batch runs. Signals entry changes when the queue moves. */

import { signal } from '@preact/signals';
import { AppError, hintForCode, isAbort, toAppError } from '../api/errors';
import type { BatchRequested, QueueSnapshot } from '../api/types';
import { api, auth, bumpData } from './app';
import { toast } from './toasts';

export interface QueueState {
  readonly snapshot: QueueSnapshot | null;
  readonly error: AppError | null;
  readonly fetchedAt: number | null;
  /** Server clock minus browser clock when the snapshot arrived (0 when unknown). */
  readonly skewMs: number;
}

const EMPTY_QUEUE: QueueState = { snapshot: null, error: null, fetchedAt: null, skewMs: 0 };

export const queueState = signal<QueueState>(EMPTY_QUEUE);

export interface BatchRequestState {
  readonly pending: boolean;
  readonly error: AppError | null;
}

export const batchRequest = signal<BatchRequestState>({ pending: false, error: null });

/** Whether the queue sheet is open; batch-request failures open it so their code is visible. */
export const queueSheetOpen = signal(false);

export function openQueueSheet(): void {
  queueSheetOpen.value = true;
  pokeQueue();
}

const FAST_MS = 2500;
const SLOW_MS = 20_000;
/** After a batch request, poll quickly until the worker has had time to pick it up. */
const REQUEST_WINDOW_MS = 20_000;
let fastUntil = 0;

/** Heartbeat age beyond which a server that does not report `worker.stale` is assumed stopped. */
const FALLBACK_STALE_MS = 15_000;

/**
 * True when the worker is not running. The server's own judgement wins while
 * the snapshot is fresh; once it is older than the staleness window (polls
 * failing), the heartbeat age decides, measured on the server's clock (browser
 * time plus the skew seen in the Date header) so a wrong browser clock cannot
 * flip it.
 */
export function workerStale(snapshot: QueueSnapshot, browserNow: number = Date.now(), skewMs = 0, fetchedAt: number = browserNow): boolean {
  if (snapshot.worker.stale !== null && browserNow - fetchedAt <= FALLBACK_STALE_MS) return snapshot.worker.stale;
  const seen = snapshot.worker.last_seen_at;
  if (seen === null) return true;
  const at = Date.parse(seen);
  return Number.isNaN(at) || browserNow + skewMs - at > FALLBACK_STALE_MS;
}

/** Work is moving or about to: poll quickly. A request waiting on a stopped worker is not about to move. */
export function isBusy(snapshot: QueueSnapshot, stale = false): boolean {
  return snapshot.batch.running || snapshot.counts.running > 0 || (snapshot.batch.request_pending && !stale);
}

function signature(snapshot: QueueSnapshot): string {
  const items = snapshot.items.map((item) => `${item.job_id}:${item.status}`).join(',');
  return `${String(snapshot.batch.running)}|${snapshot.batch.last_finished_at ?? ''}|${items}`;
}

let lastSignature: string | null = null;
let timer: number | null = null;
let inFlight: AbortController | null = null;

export async function refreshQueue(): Promise<void> {
  if (auth.value !== 'signed-in') return;
  inFlight?.abort();
  const controller = new AbortController();
  inFlight = controller;
  try {
    const { snapshot, serverTimeMs } = await api().queueStatus(controller.signal);
    const received = Date.now();
    const next = signature(snapshot);
    if (lastSignature !== null && next !== lastSignature) bumpData();
    lastSignature = next;
    queueState.value = { snapshot, error: null, fetchedAt: received, skewMs: serverTimeMs === null ? 0 : serverTimeMs - received };
  } catch (error) {
    if (isAbort(error)) return;
    queueState.value = { ...queueState.value, error: toAppError(error, 'Load the queue') };
  } finally {
    if (inFlight === controller) inFlight = null;
  }
}

function schedule(): void {
  if (timer !== null) window.clearTimeout(timer);
  const { snapshot, skewMs, fetchedAt } = queueState.value;
  const now = Date.now();
  const busy = snapshot !== null && isBusy(snapshot, workerStale(snapshot, now, skewMs, fetchedAt ?? now));
  const delay = busy || now < fastUntil ? FAST_MS : SLOW_MS;
  timer = window.setTimeout(() => {
    void tick();
  }, delay);
}

async function tick(): Promise<void> {
  if (document.visibilityState === 'visible') await refreshQueue();
  schedule();
}

export function startQueuePolling(): () => void {
  const onVisible = (): void => {
    if (document.visibilityState === 'visible') void tick();
  };
  document.addEventListener('visibilitychange', onVisible);
  void tick();
  return () => {
    document.removeEventListener('visibilitychange', onVisible);
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    inFlight?.abort();
  };
}

/** Refreshes now and keeps polling at the pace the new state needs. */
export function pokeQueue(): void {
  void tick();
}

/**
 * Asks the worker for a batch. Every caller goes through here, so the queue is
 * polled quickly while the worker picks the request up.
 */
export async function requestBatch(): Promise<BatchRequested> {
  try {
    const result = await api().runBatch();
    if (result.worker?.stale !== true) fastUntil = Date.now() + REQUEST_WINDOW_MS;
    return result;
  } finally {
    pokeQueue();
  }
}

/**
 * Requests a batch and reports the outcome: a short confirmation on success
 * (unless `quiet`), or the error with its code in the queue sheet, which opens.
 * Resolves true when the worker will pick the request up.
 */
export async function runBatchNow(options: { readonly quiet?: boolean } = {}): Promise<boolean> {
  if (batchRequest.value.pending) return false;
  batchRequest.value = { pending: true, error: null };
  let error: AppError | null = null;
  let workerStopped = false;
  try {
    const result = await requestBatch();
    // Accepted either way; with a stopped worker the request waits, which the sheet explains.
    workerStopped = result.worker?.stale === true;
    if (!workerStopped && options.quiet !== true) toast('Batch requested; the worker starts it within a few seconds');
  } catch (caught) {
    // BATCH_ALREADY_RUNNING included: the hint says to run it again after the current one.
    error = toAppError(caught, 'Run batch');
  }
  batchRequest.value = { pending: false, error };
  if (error !== null || workerStopped) openQueueSheet();
  return error === null && !workerStopped;
}

/** The last batch's failure as a displayable error, or null. */
export function lastBatchError(snapshot: QueueSnapshot): AppError | null {
  const failure = snapshot.batch.last_error;
  if (failure === null) return null;
  const details = [{ label: 'Code', value: failure.code }];
  if (snapshot.batch.last_finished_at !== null) details.push({ label: 'Finished', value: snapshot.batch.last_finished_at });
  return new AppError({
    code: failure.code,
    message: `The last batch failed: ${failure.detail}`,
    hint: hintForCode(failure.code, null),
    details,
  });
}

/** Forgets everything about the queue, for sign-out. */
export function resetQueue(): void {
  inFlight?.abort();
  lastSignature = null;
  fastUntil = 0;
  queueState.value = EMPTY_QUEUE;
  batchRequest.value = { pending: false, error: null };
  queueSheetOpen.value = false;
}

export function clearBatchError(): void {
  if (batchRequest.value.error !== null) batchRequest.value = { ...batchRequest.value, error: null };
}
