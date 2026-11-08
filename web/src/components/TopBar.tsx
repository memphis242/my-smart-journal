/** Sticky top bar: pages, queue state, menu, theme switch (always at the top right). */

import type { JSX } from 'preact';
import { useRef, useState } from 'preact/hooks';
import { toAppError, type AppError } from '../api/errors';
import { WIDE, useMediaQuery } from '../hooks/useMediaQuery';
import { api, auth, isMock } from '../state/app';
import { navigate, page, type Page } from '../state/route';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { Popover, hidePopover } from './Popover';
import { QueueIndicator } from './Queue';
import { ThemeSwitch } from './ThemeSwitch';

const PAGES: readonly { readonly page: Page; readonly label: string; readonly hint: string }[] = [
  { page: 'entries', label: 'Entries', hint: 'Browse, search, select and export entries' },
  { page: 'compilations', label: 'Compilations', hint: 'Resume bullets, STAR stories and open threads compiled from selected entries' },
];

function NavLink({ target, label, hint }: { readonly target: Page; readonly label: string; readonly hint: string }): JSX.Element {
  const current = page.value === target;
  return (
    <a
      class={current ? 'nav-link nav-current' : 'nav-link'}
      href={target === 'entries' ? '/' : '/compilations'}
      aria-current={current ? 'page' : undefined}
      title={hint}
      data-testid={`nav-${target}`}
      onClick={(event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        navigate(target);
      }}
    >
      {label}
    </a>
  );
}

function AppMenu(): JSX.Element {
  const anchor = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState<AppError | null>(null);
  const [pending, setPending] = useState(false);
  const signOut = async (): Promise<void> => {
    setPending(true);
    setError(null);
    try {
      await api().signOut();
      hidePopover('app-menu');
      auth.value = 'signed-out';
    } catch (caught) {
      setError(toAppError(caught, 'Sign out'));
    } finally {
      setPending(false);
    }
  };
  return (
    <>
      <button
        type="button"
        class="icon-button"
        ref={anchor}
        popovertarget="app-menu"
        title="Menu"
        aria-label="Menu"
        data-testid="app-menu-button"
      >
        <Icon name="more" size={18} />
      </button>
      <Popover id="app-menu" anchor={anchor} label="Menu" align="end" class="menu">
        <button type="button" class="menu-item" disabled={pending} onClick={() => void signOut()} data-testid="sign-out">
          <Icon name="sign-out" />
          <span>{pending ? 'Signing out' : 'Sign out'}</span>
        </button>
        {error !== null && (
          <div class="menu-error">
            <ErrorNotice error={error} compact />
          </div>
        )}
      </Popover>
    </>
  );
}

export function TopBar(): JSX.Element {
  const wide = useMediaQuery(WIDE);
  const signedIn = auth.value === 'signed-in';
  return (
    <header class="topbar" data-testid="topbar">
      <div class="topbar-inner">
        {(wide || !signedIn) && (
          <span class="wordmark" title="my-smart-journal">
            Journal
          </span>
        )}
        {signedIn && (
          <nav class="nav" aria-label="Pages">
            {PAGES.map((item) => (
              <NavLink key={item.page} target={item.page} label={item.label} hint={item.hint} />
            ))}
          </nav>
        )}
        <div class="topbar-spacer" />
        {isMock && wide && (
          <span class="chip chip-mock" title="Served by the in-browser mock API; nothing is saved">
            Mock data
          </span>
        )}
        {signedIn && <QueueIndicator />}
        {signedIn && <AppMenu />}
        <ThemeSwitch />
      </div>
    </header>
  );
}
