/**
 * Unsent writing kept in this browser: the new-entry draft, and per entry the
 * edits not yet saved. Drafts survive closing the dialog, a reload and a lost
 * session (sign in again and reopen); they expire after 7 days, and signing
 * out clears them.
 *
 * An edit draft stores the entry as it was when editing began (the base, with
 * its version) and only the fields the user changed, so restoring it never
 * overwrites newer content in fields the user did not touch.
 */

import { prefKeys, readJsonPref, writePref } from './prefs';

export interface DraftFields {
  readonly text: string;
  readonly title: string;
  readonly tags: readonly string[];
  readonly date: string;
}

export type FieldName = keyof DraftFields;
export const FIELD_NAMES: readonly FieldName[] = ['title', 'date', 'tags', 'text'];

export interface NewDraft extends DraftFields {
  readonly savedAt: number;
}

export interface DraftBase extends DraftFields {
  readonly version: number | null;
  readonly revision: number;
}

export interface EditDraft {
  readonly id: string;
  readonly base: DraftBase;
  readonly changes: Partial<DraftFields>;
  readonly savedAt: number;
}

export const DRAFT_TTL_MS = 7 * 24 * 3600 * 1000;
export const MAX_EDIT_DRAFTS = 20;
const NEW_KEY = 'draft';
const EDIT_PREFIX = 'edit-draft:';

// ------------------------------------------------------------------ field logic (pure)

function sameField(name: FieldName, a: DraftFields, b: DraftFields): boolean {
  switch (name) {
    case 'tags':
      return a.tags.join('\u0000') === b.tags.join('\u0000');
    case 'text':
    case 'title':
      return a[name].trim() === b[name].trim();
    case 'date':
      return a.date === b.date;
  }
}

/** Fields whose values differ between two snapshots. */
export function differingFields(a: DraftFields, b: DraftFields): readonly FieldName[] {
  return FIELD_NAMES.filter((name) => !sameField(name, a, b));
}

/** Only the fields the user changed relative to the base. */
export function changedFields(base: DraftFields, values: DraftFields): Partial<DraftFields> {
  const changes: { -readonly [K in FieldName]?: DraftFields[K] } = {};
  for (const name of differingFields(base, values)) {
    if (name === 'tags') changes.tags = values.tags;
    else changes[name] = values[name];
  }
  return changes;
}

export function applyChanges(current: DraftFields, changes: Partial<DraftFields>): DraftFields {
  return { ...current, ...changes };
}

/**
 * After the entry changed elsewhere: keep what the user changed relative to the
 * old base, and take the new server values for every field the user left alone.
 */
export function reapply(oldBase: DraftFields, newBase: DraftFields, values: DraftFields): DraftFields {
  return applyChanges(newBase, changedFields(oldBase, values));
}

/** True when the entry is still the one the draft was started from. */
export function sameBase(base: DraftBase, current: DraftBase): boolean {
  if (base.version !== null && current.version !== null) return base.version === current.version;
  return base.revision === current.revision && differingFields(base, current).length === 0;
}

// ------------------------------------------------------------------ storage

function isFields(value: unknown): value is DraftFields {
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

function isNewDraft(value: unknown): value is NewDraft {
  return isFields(value) && typeof (value as unknown as Record<string, unknown>)['savedAt'] === 'number';
}

function isEditDraft(value: unknown): value is EditDraft {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  const base = record['base'];
  const changes = record['changes'];
  return (
    typeof record['id'] === 'string' &&
    typeof record['savedAt'] === 'number' &&
    isFields(base) &&
    typeof (base as unknown as Record<string, unknown>)['revision'] === 'number' &&
    typeof changes === 'object' &&
    changes !== null
  );
}

function expired(savedAt: number, now: number): boolean {
  return now - savedAt > DRAFT_TTL_MS;
}

export function readNewDraft(now: number = Date.now()): NewDraft | null {
  const draft = readJsonPref(NEW_KEY, isNewDraft);
  if (draft === null) return null;
  if (expired(draft.savedAt, now)) {
    // Dropping an expired copy; a failed removal leaves it to expire again on the next read.
    void writePref(NEW_KEY, null);
    return null;
  }
  return draft;
}

/** Keeps the draft (an empty one is removed). Returns false when the browser could not store it. */
export function saveNewDraft(fields: DraftFields | null, now: number = Date.now()): boolean {
  const empty = fields === null || (fields.text.trim() === '' && fields.title.trim() === '');
  return writePref(NEW_KEY, empty ? null : JSON.stringify({ ...fields, savedAt: now }));
}

export function readEditDraft(id: string, now: number = Date.now()): EditDraft | null {
  const draft = readJsonPref(EDIT_PREFIX + id, isEditDraft);
  if (draft?.id !== id) return null;
  if (expired(draft.savedAt, now)) {
    void writePref(EDIT_PREFIX + id, null);
    return null;
  }
  return draft;
}

/** Drops expired drafts and, beyond the cap, the oldest ones. */
function pruneEditDrafts(keep: string, now: number): void {
  const drafts = prefKeys(EDIT_PREFIX).flatMap((key) => {
    const draft = readJsonPref(key, isEditDraft);
    return draft === null ? [{ key, savedAt: 0 }] : [{ key, savedAt: draft.savedAt }];
  });
  const live = drafts.filter((draft) => {
    if (draft.key === EDIT_PREFIX + keep || !expired(draft.savedAt, now)) return true;
    void writePref(draft.key, null);
    return false;
  });
  live.sort((a, b) => b.savedAt - a.savedAt);
  for (const draft of live.slice(MAX_EDIT_DRAFTS)) {
    if (draft.key !== EDIT_PREFIX + keep) void writePref(draft.key, null);
  }
}

/** Stores the edits of one entry (none: removes them). Returns false when the browser could not store them. */
export function saveEditDraft(id: string, base: DraftBase, values: DraftFields, now: number = Date.now()): boolean {
  const changes = changedFields(base, values);
  if (Object.keys(changes).length === 0) return writePref(EDIT_PREFIX + id, null);
  const draft: EditDraft = { id, base, changes, savedAt: now };
  const stored = writePref(EDIT_PREFIX + id, JSON.stringify(draft));
  if (stored) pruneEditDrafts(id, now);
  return stored;
}

export function dropEditDraft(id: string): void {
  // A copy that cannot be removed expires on its own.
  void writePref(EDIT_PREFIX + id, null);
}

export function clearDrafts(): void {
  void writePref(NEW_KEY, null);
  for (const key of prefKeys(EDIT_PREFIX)) void writePref(key, null);
}
