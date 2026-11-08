/** Light/dark theme: follows the system until the user picks one, then persists the pick. */

import { signal } from '@preact/signals';
import { readPref, writePref } from './prefs';

export type Theme = 'light' | 'dark';

/** Same key as public/theme-init.js, which applies it before the first paint. */
const THEME_KEY = 'theme';
const THEME_COLORS: Readonly<Record<Theme, string>> = { light: '#f4f2ec', dark: '#151618' };

function systemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function storedTheme(): Theme | null {
  const value = readPref(THEME_KEY);
  return value === 'light' || value === 'dark' ? value : null;
}

function initialTheme(): Theme {
  const attribute = document.documentElement.getAttribute('data-theme');
  if (attribute === 'light' || attribute === 'dark') return attribute;
  return storedTheme() ?? systemTheme();
}

export const theme = signal<Theme>(initialTheme());

function apply(next: Theme): void {
  document.documentElement.setAttribute('data-theme', next);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLORS[next]);
}

export function setTheme(next: Theme): void {
  theme.value = next;
  apply(next);
  // A failed write only means the choice is not remembered next visit.
  void writePref(THEME_KEY, next);
}

/** Keeps following the system preference while the user has not chosen. */
export function followSystemTheme(): () => void {
  const query = window.matchMedia('(prefers-color-scheme: dark)');
  const onChange = (): void => {
    if (storedTheme() !== null) return;
    const next = systemTheme();
    theme.value = next;
    apply(next);
  };
  query.addEventListener('change', onChange);
  return () => {
    query.removeEventListener('change', onChange);
  };
}
