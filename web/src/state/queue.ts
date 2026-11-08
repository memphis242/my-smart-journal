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
}

export const queueState = signal<QueueState>({ snapshot: null, error: null, fetchedAt: null });

export interface BatchRequestState {
  readonly pending: boolean;
  readonly error: AppError | null;
}

export const batchRequest = signal<BatchRequestState>({ pending: false, error: null });

const FAST_MS = 2500;
const SLOW_MS = 20_000;
/** After a batch request, poll quickly until the worker has had time to pick it up. */
const REQUEST_WINDOW_MS = 20_000;
let fastUntil = 0;

/** No heartbeat within twice the interval means the worker is not running. */
export function workerStale(snapshot: QueueSnapshot, now: number = Date.now()): boolean {
  const seen = snapshot.worker.last_seen_at;
  if (seen === null) return true;
  const at = Date.parse(seen);
  return Number.isNaN(at) || now - at > 2 * snapshot.worker.interval_s * 1000;
}

export function isBusy(snapshot: QueueSnapshot): boolean {
  return snapshot.batch.running || snapshot.counts.running > 0;
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
    const snapshot = await api().queue(controller.signal);
    const next = signature(snapshot);
    if (lastSignature !== null && next !== lastSignature) bumpData();
    lastSignature = next;
    queueState.value = { snapshot, error: null, fetchedAt: Date.now() };
  } catch (error) {
    if (isAbort(error)) return;
    queueState.value = { ...queueState.value, error: toAppError(error, 'Load the queue') };
  } finally {
    if (inFlight === controller) inFlight = null;
  }
}

function schedule(): void {
  if (timer !== null) window.clearTimeout(timer);
  const snapshot = queueState.value.snapshot;
  const delay = (snapshot !== null && isBusy(snapshot)) || Date.now() < fastUntil ? FAST_MS : SLOW_MS;
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

export async function runBatchNow(): Promise<void> {
  if (batchRequest.value.pending) return;
  batchRequest.value = { pending: true, error: null };
  try {
    const result = await requestBatch();
    if (result.worker?.stale === true) {
      batchRequest.value = {
        pending: false,
        error: new AppError({
          code: 'WORKER_NOT_RUNNING',
          message: 'Batch requested, but the worker is not running, so the batch will not start.',
          hint: hintForCode('WORKER_NOT_RUNNING', null),
        }),
      };
    } else {
      batchRequest.value = { pending: false, error: null };
      toast('Batch requested; the worker starts it within a few seconds');
    }
  } catch (error) {
    // BATCH_ALREADY_RUNNING included: the code stays visible next to the button while the queue shows the batch.
    batchRequest.value = { pending: false, error: toAppError(error, 'Run batch') };
  }
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

export function clearBatchError(): void {
  if (batchRequest.value.error !== null) batchRequest.value = { ...batchRequest.value, error: null };
}
