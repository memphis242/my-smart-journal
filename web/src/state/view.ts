/**
 * The entries page's view: date range, facet filters, search query and mode,
 * order. Mirrored into the URL query so a reload or a shared link restores it.
 */

import { computed, signal } from '@preact/signals';
import type { EntryFilters, SortOrder } from '../api/types';
import { isIsoDate, localDate, presetBounds, RANGE_KEYS, type DateBounds, type RangeKey } from '../lib/dates';
import { readPref, writePref } from '../lib/prefs';

export type SearchMode = 'keyword' | 'relevance';
export type Density = 'lines' | 'summaries';

export interface EntriesView {
  readonly range: RangeKey;
  /** Used when range is "custom". */
  readonly custom: DateBounds;
  readonly tags: readonly string[];
  readonly projects: readonly string[];
  readonly skills: readonly string[];
  readonly q: string;
  readonly mode: SearchMode;
  readonly order: SortOrder;
}

const DEFAULT_VIEW: EntriesView = {
  range: 'month',
  custom: { from: null, to: null },
  tags: [],
  projects: [],
  skills: [],
  q: '',
  mode: 'keyword',
  order: 'desc',
};

function fromUrl(search: string): EntriesView {
  const params = new URLSearchParams(search);
  const range = params.get('range');
  const from = params.get('from');
  const to = params.get('to');
  const mode = params.get('mode');
  const order = params.get('order');
  const validRange = RANGE_KEYS.find((key) => key === range);
  return {
    range: validRange ?? DEFAULT_VIEW.range,
    custom: {
      from: from !== null && isIsoDate(from) ? from : null,
      to: to !== null && isIsoDate(to) ? to : null,
    },
    tags: params.getAll('tag'),
    projects: params.getAll('project'),
    skills: params.getAll('skill'),
    q: params.get('q') ?? '',
    mode: mode === 'relevance' ? 'relevance' : 'keyword',
    order: order === 'asc' ? 'asc' : 'desc',
  };
}

/** Mock-mode switches (mock-*) stay in every address so a reload keeps them. */
export function mockQuery(): string {
  const params = new URLSearchParams();
  for (const [key, value] of new URLSearchParams(window.location.search)) {
    if (key.startsWith('mock-')) params.append(key, value);
  }
  const text = params.toString();
  return text === '' ? '' : `?${text}`;
}

function toUrl(view: EntriesView): string {
  const params = new URLSearchParams();
  if (view.range !== DEFAULT_VIEW.range) params.set('range', view.range);
  if (view.range === 'custom') {
    if (view.custom.from !== null) params.set('from', view.custom.from);
    if (view.custom.to !== null) params.set('to', view.custom.to);
  }
  for (const tag of view.tags) params.append('tag', tag);
  for (const project of view.projects) params.append('project', project);
  for (const skill of view.skills) params.append('skill', skill);
  if (view.q !== '') params.set('q', view.q);
  if (view.mode !== 'keyword') params.set('mode', view.mode);
  if (view.order !== 'desc') params.set('order', view.order);
  // Mock-mode switches (mock-*) stay in the address so a reload keeps them.
  for (const [key, value] of new URLSearchParams(window.location.search)) {
    if (key.startsWith('mock-')) params.append(key, value);
  }
  const text = params.toString();
  return text === '' ? '/' : `/?${text}`;
}

export const view = signal<EntriesView>(fromUrl(window.location.search));

function onEntriesPage(): boolean {
  return window.location.pathname === '/' || window.location.pathname === '';
}

/** The Entries address for the current view, for navigation back to Entries. */
export function entriesUrl(): string {
  return toUrl(view.value);
}

export function updateView(patch: Partial<EntriesView>): void {
  const next = { ...view.value, ...patch };
  view.value = next;
  if (onEntriesPage()) window.history.replaceState(window.history.state, '', toUrl(next));
}

/** Back and forward: take the view from the address again. */
export function syncViewFromUrl(): void {
  if (onEntriesPage()) view.value = fromUrl(window.location.search);
}

/** Scroll position per entries view, restored when the user comes back to the same view. */
export const savedScroll = new Map<string, { readonly top: number; readonly loaded: number }>();

/** Today's local date, refreshed when the tab becomes visible again. */
export const today = signal(localDate());

export function watchDayChange(): () => void {
  const onVisible = (): void => {
    const now = localDate();
    if (now !== today.value) today.value = now;
  };
  document.addEventListener('visibilitychange', onVisible);
  const timer = window.setInterval(onVisible, 60_000);
  return () => {
    document.removeEventListener('visibilitychange', onVisible);
    window.clearInterval(timer);
  };
}

export const bounds = computed<DateBounds>(() => {
  const current = view.value;
  return current.range === 'custom' ? current.custom : presetBounds(current.range, today.value);
});

export const filters = computed<EntryFilters>(() => {
  const current = view.value;
  const range = bounds.value;
  const result: { -readonly [K in keyof EntryFilters]: EntryFilters[K] } = {};
  if (range.from !== null) result.from = range.from;
  if (range.to !== null) result.to = range.to;
  if (current.tags.length > 0) result.tag = current.tags;
  if (current.projects.length > 0) result.project = current.projects;
  if (current.skills.length > 0) result.skill = current.skills;
  return result;
});

export const facetFilterCount = computed(
  () => view.value.tags.length + view.value.projects.length + view.value.skills.length,
);

const DENSITY_KEY = 'density';
export const density = signal<Density>(readPref(DENSITY_KEY) === 'summaries' ? 'summaries' : 'lines');

export function setDensity(next: Density): void {
  density.value = next;
  // Not remembering the choice is harmless.
  void writePref(DENSITY_KEY, next);
}
