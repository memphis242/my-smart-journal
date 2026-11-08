/** Two peer pages, addressed by path; the server answers unknown paths with index.html. */

import { signal } from '@preact/signals';

export type Page = 'entries' | 'compilations';

function pageFromPath(pathname: string): Page {
  return pathname.replace(/\/+$/, '') === '/compilations' ? 'compilations' : 'entries';
}

export const page = signal<Page>(pageFromPath(window.location.pathname));

export function navigate(next: Page): void {
  if (page.value === next) return;
  const path = next === 'compilations' ? '/compilations' : '/';
  window.history.pushState({ page: next }, '', path + (next === 'entries' ? window.location.search : ''));
  page.value = next;
}

export function listenToHistory(): () => void {
  const onPop = (): void => {
    page.value = pageFromPath(window.location.pathname);
  };
  window.addEventListener('popstate', onPop);
  return () => {
    window.removeEventListener('popstate', onPop);
  };
}
