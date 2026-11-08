/**
 * Which entry the detail modal shows, and the ordered list it was opened from,
 * so the side bars can step to the next newer and the next older entry.
 */

import { signal } from '@preact/signals';
import type { EntryDetail, EntryFilters, EntrySummary } from '../api/types';
import { assert } from '../lib/assert';

export interface NavContext {
  /** Ids ordered newest first. */
  readonly newestFirst: readonly string[];
  /** Summaries for the ids above, for instant rendering while details load. */
  readonly previews: ReadonlyMap<string, EntrySummary>;
  /** Filters of the list, for GET /entries/{id}/neighbors past the loaded rows. */
  readonly filters: EntryFilters;
  /** False when the server has more matching entries than are loaded. */
  readonly complete: boolean;
}

export interface OpenEntry {
  readonly id: string;
  readonly context: NavContext;
  /** -1 moved to an older entry, 1 to a newer one, 0 opened directly. */
  readonly direction: -1 | 0 | 1;
}

export const openEntry = signal<OpenEntry | null>(null);

/** Entries deleted in this session; result lists drop them at once, before any refresh. */
export const deletedEntries = signal<ReadonlySet<string>>(new Set());

export function markDeleted(id: string): void {
  deletedEntries.value = new Set([...deletedEntries.value, id]);
}

export function showEntry(id: string, context: NavContext): void {
  assert(context.newestFirst.includes(id) || !context.complete || context.newestFirst.length === 0, 'an entry opened from a complete list is in it');
  openEntry.value = { id, context, direction: 0 };
}

export function moveEntry(id: string, direction: -1 | 1): void {
  const current = openEntry.value;
  if (current === null) return;
  openEntry.value = { id, context: current.context, direction };
}

export function closeEntry(): void {
  openEntry.value = null;
}

export function contextFor(entries: readonly EntrySummary[], newestFirst: boolean, filters: EntryFilters, complete: boolean): NavContext {
  const ordered = newestFirst ? entries : [...entries].reverse();
  return {
    newestFirst: ordered.map((entry) => entry.id),
    previews: new Map(entries.map((entry) => [entry.id, entry])),
    filters,
    complete,
  };
}

/** Chronological context for search results, which arrive ranked, not dated. */
export function chronologicalContext(entries: readonly EntrySummary[]): NavContext {
  const sorted = [...entries].sort((a, b) =>
    a.entry_date !== b.entry_date
      ? b.entry_date.localeCompare(a.entry_date)
      : a.created_at !== b.created_at
        ? b.created_at.localeCompare(a.created_at)
        : b.id.localeCompare(a.id),
  );
  return {
    newestFirst: sorted.map((entry) => entry.id),
    previews: new Map(entries.map((entry) => [entry.id, entry])),
    filters: {},
    complete: true,
  };
}

/** Details fetched recently, keyed by id, with the data version they were fetched at. */
const details = new Map<string, { readonly detail: EntryDetail; readonly version: number }>();
const MAX_DETAILS = 60;

/** A detail fetched at this data version, or null. */
export function cachedDetail(id: string, version: number): EntryDetail | null {
  const hit = details.get(id);
  return hit?.version === version ? hit.detail : null;
}

/** The latest detail fetched for the id, however old, to show while a fresh one loads. */
export function peekDetail(id: string): EntryDetail | null {
  return details.get(id)?.detail ?? null;
}

export function storeDetail(detail: EntryDetail, version: number): void {
  details.delete(detail.id);
  details.set(detail.id, { detail, version });
  if (details.size > MAX_DETAILS) {
    const oldest = details.keys().next();
    if (oldest.done !== true) details.delete(oldest.value);
  }
}

export function clearDetails(): void {
  details.clear();
}
