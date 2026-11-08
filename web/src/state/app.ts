/** Process-wide state: the API client, sign-in state and the data version. */

import { signal } from '@preact/signals';
import { ApiClient, type FetchLike } from '../api/client';
import type { ApiError } from '../api/errors';
import { assert } from '../lib/assert';

export type AuthState = 'checking' | 'signed-in' | 'signed-out';

export const auth = signal<AuthState>('checking');

/** Why the session ended, when the server said so (for example TOKEN_REISSUE_REQUIRED). */
export const authReason = signal<ApiError | null>(null);

/** Incremented whenever entries change, so lists, facets and details reload. */
export const dataVersion = signal(0);

export function bumpData(): void {
  dataVersion.value += 1;
}

export const isMock = import.meta.env.MODE === 'mock';

let client: ApiClient | null = null;

export function initApi(fetchImpl: FetchLike): ApiClient {
  assert(client === null, 'the API client is created once');
  client = new ApiClient({
    fetch: fetchImpl,
    onUnauthorized: (error) => {
      authReason.value = error;
      auth.value = 'signed-out';
    },
  });
  return client;
}

export function api(): ApiClient {
  assert(client !== null, 'initApi ran before the first request');
  return client;
}
