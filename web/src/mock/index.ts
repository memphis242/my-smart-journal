import type { FetchLike } from '../api/client';
import { SEED_COMPILATIONS } from './seed-compilations';
import { SEED_CONCEPTS } from './seed-concepts';
import { SEED_ENTRIES } from './seed-entries';
import { EMBEDDINGS_MODES } from './search';
import { createMockApi } from './server';

/** Installs a private mock transport; no window.fetch replacement or network access is needed. */
export function installMock(): FetchLike {
  const params = new URLSearchParams(window.location.search);
  const duration = (key: string, fallback: number): number => {
    const value = params.get(key);
    if (value === null) return fallback;
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0 || value.trim() === '') {
      throw new Error(`Mock option ${key}=${JSON.stringify(value)} is invalid. Use a non-negative number of milliseconds.`);
    }
    return parsed;
  };
  const session = {
    get: (): boolean => window.sessionStorage.getItem('msj.mock.session') === '1',
    set: (value: boolean): void => {
      window.sessionStorage.setItem('msj.mock.session', value ? '1' : '0');
    },
  };
  const empty = params.get('mock-empty') === '1';
  const embeddingsParam = params.get('mock-embeddings') ?? 'ok';
  const embeddings = EMBEDDINGS_MODES.find((mode) => mode === embeddingsParam);
  if (embeddings === undefined) {
    throw new Error(`Mock option mock-embeddings=${JSON.stringify(embeddingsParam)} is invalid. Use one of: ${EMBEDDINGS_MODES.join(', ')}.`);
  }
  const fetch = createMockApi({
    seeds: empty ? [] : SEED_ENTRIES,
    compilations: empty ? [] : SEED_COMPILATIONS,
    concepts: SEED_CONCEPTS,
    latencyMs: duration('mock-latency', 150),
    jobDelayMs: duration('mock-job-ms', 700),
    pickupDelayMs: duration('mock-pickup-ms', 1500),
    searchDelayMs: duration('mock-search-ms', 60),
    workerStale: params.get('mock-worker') === 'stale',
    batchError: params.get('mock-batch-error') === '1',
    tokenRotated: params.get('mock-token') === 'rotated',
    signOutFails: params.get('mock-signout') === 'fail',
    embeddings,
    ...(params.get('mock-signed-in') === '1' ? { signedIn: true } : {}),
    session,
  }).fetch;
  // Tests act as "another device" through the same in-page server (mock builds only).
  (window as unknown as { __msjMockFetch?: FetchLike }).__msjMockFetch = fetch;
  return fetch;
}
