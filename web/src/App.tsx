/** Application shell: sticky top bar over one page; dialogs live in the top layer. */

import { effect, signal } from '@preact/signals';
import type { JSX } from 'preact';
import { useEffect } from 'preact/hooks';
import { isAbort, toAppError, type AppError } from './api/errors';
import { ErrorNotice } from './components/ErrorNotice';
import { QueueSheet } from './components/Queue';
import { SignInDialog } from './components/SignInDialog';
import { Toasts } from './components/Toasts';
import { TopBar } from './components/TopBar';
import { EntryModal } from './dialogs/EntryModal';
import { followSystemTheme } from './lib/theme';
import { CompilationsPage } from './pages/CompilationsPage';
import { EntriesPage } from './pages/entries/EntriesPage';
import { api, auth, authReason, dataVersion } from './state/app';
import { closeEntry } from './state/entry-modal';
import { loadFacets } from './state/facets';
import { pokeQueue, startQueuePolling } from './state/queue';
import { listenToHistory, page } from './state/route';
import { watchDayChange } from './state/view';


const startupError = signal<AppError | null>(null);

async function checkSession(): Promise<void> {
  try {
    const problem = await api().sessionProblem();
    // A plain "not signed in" needs no explanation; a rotated token does.
    if (problem !== null && problem.code !== 'UNAUTHORIZED') authReason.value = problem;
    auth.value = problem === null ? 'signed-in' : 'signed-out';
    startupError.value = null;
    if (problem === null) pokeQueue();
  } catch (error) {
    if (isAbort(error)) return;
    startupError.value = toAppError(error, 'Check sign-in');
  }
}

export function App(): JSX.Element {
  useEffect(() => {
    void checkSession();
    const stops = [followSystemTheme(), listenToHistory(), watchDayChange(), startQueuePolling()];
    const stopFacets = effect(() => {
      // Reload facets whenever entries change while signed in.
      if (auth.value === 'signed-in' && dataVersion.value >= 0) void loadFacets();
    });
    const stopSignedOut = effect(() => {
      if (auth.value === 'signed-out') closeEntry();
    });
    return () => {
      for (const stop of stops) stop();
      stopFacets();
      stopSignedOut();
    };
  }, []);

  const state = auth.value;
  return (
    <div class="app">
      <TopBar />
      {state === 'signed-in' ? (
        page.value === 'compilations' ? (
          <CompilationsPage />
        ) : (
          <EntriesPage />
        )
      ) : (
        <main class="page page-blank">
          {startupError.value !== null && (
            <div class="list-error">
              <ErrorNotice error={startupError.value} onRetry={() => void checkSession()} />
            </div>
          )}
        </main>
      )}
      {state === 'signed-out' && <SignInDialog />}
      {state === 'signed-in' && <EntryModal />}
      {state === 'signed-in' && <QueueSheet />}
      <Toasts />
    </div>
  );
}
