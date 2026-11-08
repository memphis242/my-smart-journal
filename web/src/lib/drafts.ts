/**
 * Unsent writing kept in this browser: the new-entry draft, and the edits of
 * one entry. Both survive closing the dialog, a reload, and a lost session
 * (sign in again and reopen); signing out clears them.
 */

import { readJsonPref, writePref } from './prefs';

export interface Draft {
  readonly text: string;
  readonly title: string;
  readonly tags: readonly string[];
  readonly date: string;
}

export interface EditDraft extends Draft {
  readonly id: string;
  /** Revision the edits started from. */
  readonly revision: number;
}

const NEW_KEY = 'draft';
const EDIT_KEY = 'edit-draft';

function isDraft(value: unknown): value is Draft {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record['text'] === 'string' &&
    typeof record['title'] === 'string' &&
    typeof record['date'] === 'string' &&
    Array.isArray(record['tags']) &&
    record['tags'].every((tag) => typeof tag === 'string')
  );
}

function isEditDraft(value: unknown): value is EditDraft {
  if (!isDraft(value)) return false;
  const record = value as unknown as Record<string, unknown>;
  return typeof record['id'] === 'string' && typeof record['revision'] === 'number';
}

export function readNewDraft(): Draft | null {
  return readJsonPref(NEW_KEY, isDraft);
}

/** Keeps the draft; an empty one is removed. A failed write only loses the browser copy. */
export function saveNewDraft(draft: Draft | null): void {
  const empty = draft === null || (draft.text.trim() === '' && draft.title.trim() === '');
  void writePref(NEW_KEY, empty ? null : JSON.stringify(draft));
}

export function readEditDraft(id: string): EditDraft | null {
  const draft = readJsonPref(EDIT_KEY, isEditDraft);
  return draft !== null && draft.id === id ? draft : null;
}

export function saveEditDraft(draft: EditDraft | null): void {
  void writePref(EDIT_KEY, draft === null ? null : JSON.stringify(draft));
}

export function clearDrafts(): void {
  saveNewDraft(null);
  saveEditDraft(null);
}
