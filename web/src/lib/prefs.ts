/**
 * Per-viewer conveniences kept in localStorage (theme, density, column width,
 * unsent drafts). Storage can be missing or throw (private windows, blocked
 * site data), so every access degrades to "no stored value".
 *
 * Storage keys are separate from CSS-facing names on purpose.
 */

const PREFIX = 'msj.';

export function readPref(key: string): string | null {
  try {
    return window.localStorage.getItem(PREFIX + key);
  } catch {
    return null;
  }
}

/** Returns false when the value could not be stored; callers may ignore that for conveniences. */
export function writePref(key: string, value: string | null): boolean {
  try {
    if (value === null) window.localStorage.removeItem(PREFIX + key);
    else window.localStorage.setItem(PREFIX + key, value);
    return true;
  } catch {
    return false;
  }
}

export function readJsonPref<T>(key: string, accept: (value: unknown) => value is T): T | null {
  const raw = readPref(key);
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return accept(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** Stored keys under `prefix` (without the msj. prefix), or none when storage is unavailable. */
export function prefKeys(prefix: string): readonly string[] {
  try {
    const keys: string[] = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (key?.startsWith(PREFIX + prefix) === true) keys.push(key.slice(PREFIX.length));
    }
    return keys;
  } catch {
    return [];
  }
}
