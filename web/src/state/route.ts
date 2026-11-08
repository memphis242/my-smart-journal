/** Two peer pages, addressed by path; the server answers unknown paths with index.html. */

import { signal } from '@preact/signals';
import { entriesUrl, mockQuery, syncViewFromUrl } from './view';

export type Page = 'entries' | 'compilations';

function pageFromPath(pathname: string): Page {
  return pathname.replace(/\/+$/, '') === '/compilations' ? 'compilations' : 'entries';
}

export const page = signal<Page>(pageFromPath(window.location.pathname));

export function navigate(next: Page): void {
  if (page.value === next) return;
  // Entries carries its view (range, filters, search) in the address, so a reload or Back restores it.
  const url = next === 'compilations' ? `/compilations${mockQuery()}` : entriesUrl();
  window.history.pushState({ page: next }, '', url);
  page.value = next;
}

export function listenToHistory(): () => void {
  const onPop = (): void => {
    page.value = pageFromPath(window.location.pathname);
    syncViewFromUrl();
  };
  window.addEventListener('popstate', onPop);
  return () => {
    window.removeEventListener('popstate', onPop);
  };
}
