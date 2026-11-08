import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DRAFT_TTL_MS,
  MAX_EDIT_DRAFTS,
  changedFields,
  clearDrafts,
  readEditDraft,
  readNewDraft,
  reapply,
  sameBase,
  saveEditDraft,
  saveNewDraft,
  type DraftBase,
} from './drafts';

class MemoryStorage {
  readonly items = new Map<string, string>();
  failWrites = false;
  get length(): number {
    return this.items.size;
  }
  key(index: number): string | null {
    return [...this.items.keys()][index] ?? null;
  }
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    if (this.failWrites) throw new DOMException('quota', 'QuotaExceededError');
    this.items.set(key, value);
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
}

const BASE: DraftBase = { title: 'Index rebuild', date: '2026-11-08', tags: ['work'], text: 'Rebuilt the index.', version: 3, revision: 2 };
const NOW = Date.parse('2026-11-08T12:00:00Z');
let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  vi.stubGlobal('window', { localStorage: storage });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('edit drafts', () => {
  it('stores only the changed fields, per entry, side by side', () => {
    expect(saveEditDraft('a', BASE, { ...BASE, title: 'New title' }, NOW)).toBe(true);
    expect(saveEditDraft('b', BASE, { ...BASE, text: 'Other text' }, NOW)).toBe(true);
    expect(readEditDraft('a', NOW)?.changes).toEqual({ title: 'New title' });
    expect(readEditDraft('b', NOW)?.changes).toEqual({ text: 'Other text' });
  });

  it('removes a draft that no longer differs from its base', () => {
    void saveEditDraft('a', BASE, { ...BASE, title: 'New title' }, NOW);
    void saveEditDraft('a', BASE, BASE, NOW);
    expect(readEditDraft('a', NOW)).toBeNull();
  });

  it('expires drafts after seven days', () => {
    void saveEditDraft('a', BASE, { ...BASE, title: 'Old' }, NOW);
    expect(readEditDraft('a', NOW + DRAFT_TTL_MS - 1)).not.toBeNull();
    expect(readEditDraft('a', NOW + DRAFT_TTL_MS + 1)).toBeNull();
    expect(storage.items.size).toBe(0);
  });

  it('keeps at most the newest drafts beyond the cap', () => {
    for (let index = 0; index <= MAX_EDIT_DRAFTS; index += 1) {
      void saveEditDraft(`e${index}`, BASE, { ...BASE, title: `T${index}` }, NOW + index);
    }
    expect(readEditDraft('e0', NOW + 100)).toBeNull();
    expect(readEditDraft(`e${MAX_EDIT_DRAFTS}`, NOW + 100)).not.toBeNull();
  });

  it('reports a write the browser refused', () => {
    storage.failWrites = true;
    expect(saveEditDraft('a', BASE, { ...BASE, title: 'X' }, NOW)).toBe(false);
    expect(saveNewDraft({ title: '', date: '2026-11-08', tags: [], text: 'Draft' }, NOW)).toBe(false);
  });

  it('clears every draft', () => {
    void saveEditDraft('a', BASE, { ...BASE, title: 'X' }, NOW);
    void saveNewDraft({ title: '', date: '2026-11-08', tags: [], text: 'Draft' }, NOW);
    clearDrafts();
    expect(readEditDraft('a', NOW)).toBeNull();
    expect(readNewDraft(NOW)).toBeNull();
  });
});

describe('draft merging', () => {
  it('matches the base by version when both have one, by content otherwise', () => {
    expect(sameBase(BASE, { ...BASE })).toBe(true);
    expect(sameBase(BASE, { ...BASE, version: 4 })).toBe(false);
    expect(sameBase({ ...BASE, version: null }, { ...BASE, version: null })).toBe(true);
    expect(sameBase({ ...BASE, version: null }, { ...BASE, version: null, text: 'Changed elsewhere' })).toBe(false);
  });

  it('reapplies only what the user changed onto the newer entry', () => {
    const typed = { ...BASE, title: 'My new title' };
    const newer = { ...BASE, text: 'Text edited on the phone.', tags: ['work', 'phone'] };
    expect(reapply(BASE, newer, typed)).toEqual({ ...newer, title: 'My new title' });
    expect(changedFields(BASE, typed)).toEqual({ title: 'My new title' });
  });
});
