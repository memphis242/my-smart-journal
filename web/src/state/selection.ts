/** Entries chosen for export or compilation; kept across views and searches. */

import { computed, signal } from '@preact/signals';
import type { EntrySummary } from '../api/types';

export const selection = signal<ReadonlyMap<string, EntrySummary>>(new Map());
/** The last plainly toggled row; shift-click selects from here. */
export const selectionAnchor = signal<string | null>(null);

export const selectedCount = computed(() => selection.value.size);

export function isSelected(id: string): boolean {
  return selection.value.has(id);
}

export function toggleSelected(entry: EntrySummary): void {
  const next = new Map(selection.value);
  if (next.has(entry.id)) next.delete(entry.id);
  else next.set(entry.id, entry);
  selection.value = next;
  selectionAnchor.value = entry.id;
}

/** Selects every row between the anchor and `entry` in the visible order. */
export function selectRange(visible: readonly EntrySummary[], entry: EntrySummary): void {
  const anchor = selectionAnchor.value;
  const end = visible.findIndex((item) => item.id === entry.id);
  const start = anchor === null ? -1 : visible.findIndex((item) => item.id === anchor);
  if (start < 0 || end < 0) {
    toggleSelected(entry);
    return;
  }
  const next = new Map(selection.value);
  const [lo, hi] = start <= end ? [start, end] : [end, start];
  for (const item of visible.slice(lo, hi + 1)) next.set(item.id, item);
  selection.value = next;
}

export function selectAll(entries: readonly EntrySummary[]): void {
  const next = new Map(selection.value);
  for (const entry of entries) next.set(entry.id, entry);
  selection.value = next;
}

export function clearSelection(): void {
  selection.value = new Map();
  selectionAnchor.value = null;
}

/** Keeps selected summaries current after a reload, and drops deleted ones. */
export function refreshSelected(entries: readonly EntrySummary[]): void {
  if (selection.value.size === 0) return;
  let changed = false;
  const next = new Map(selection.value);
  for (const entry of entries) {
    if (next.has(entry.id) && next.get(entry.id) !== entry) {
      next.set(entry.id, entry);
      changed = true;
    }
  }
  if (changed) selection.value = next;
}

export function forgetSelected(id: string): void {
  if (!selection.value.has(id)) return;
  const next = new Map(selection.value);
  next.delete(id);
  selection.value = next;
}
