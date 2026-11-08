/** Known tag, project and skill values with counts, reloaded when entries change. */

import { signal } from '@preact/signals';
import { isAbort, toAppError, type AppError } from '../api/errors';
import type { Facets } from '../api/types';
import { api } from './app';

export interface FacetState {
  readonly facets: Facets | null;
  readonly error: AppError | null;
}

export const facetState = signal<FacetState>({ facets: null, error: null });

let controller: AbortController | null = null;

export async function loadFacets(): Promise<void> {
  controller?.abort();
  const own = new AbortController();
  controller = own;
  try {
    const facets = await api().facets(own.signal);
    facetState.value = { facets, error: null };
  } catch (error) {
    if (isAbort(error)) return;
    facetState.value = { ...facetState.value, error: toAppError(error, 'Load filters') };
  }
}
