import '@fontsource/ibm-plex-sans/latin-400.css';
import '@fontsource/ibm-plex-sans/latin-500.css';
import '@fontsource/ibm-plex-sans/latin-600.css';
import '@fontsource/ibm-plex-sans/latin-400-italic.css';
import '@fontsource/ibm-plex-mono/latin-400.css';
import '@fontsource/ibm-plex-mono/latin-500.css';
import '@fontsource-variable/source-serif-4/opsz.css';
import '@fontsource-variable/source-serif-4/opsz-italic.css';
import './styles/tokens.css';
import './styles/base.css';
import './styles/layout.css';
import './styles/list.css';
import './styles/dialogs.css';

import { render } from 'preact';
import type { FetchLike } from './api/client';
import { App } from './App';
import { initApi } from './state/app';

async function transport(): Promise<FetchLike> {
  if (import.meta.env.MODE === 'mock') {
    const { installMock } = await import('./mock');
    return installMock();
  }
  return (input, init) => window.fetch(input, init);
}

const root = document.getElementById('app');
if (root === null) throw new Error('index.html has no #app element');

initApi(await transport());
render(<App />, root);
