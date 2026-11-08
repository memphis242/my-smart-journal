/** Entries: range, filters and search above one bounded, independently scrolling list. */

import type { JSX } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { CompileDialog } from '../../dialogs/CompileDialog';
import { EntryEditor } from '../../dialogs/EntryEditor';
import { ExportDialog } from '../../dialogs/ExportDialog';
import { selection } from '../../state/selection';
import { view } from '../../state/view';
import { EntryList } from './EntryList';
import type { DialogRequest } from './ListParts';
import { SearchResults } from './SearchResults';
import { Toolbar } from './Toolbar';

function typingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

export function EntriesPage(): JSX.Element {
  const [creating, setCreating] = useState(false);
  const [dialog, setDialog] = useState<DialogRequest | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const searching = view.value.q.trim() !== '';

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
      if (typingTarget(event.target) || document.querySelector('dialog[open]') !== null) return;
      if (event.key === '/') {
        event.preventDefault();
        searchRef.current?.focus();
      } else if (event.key === 'n' || event.key === 'N') {
        event.preventDefault();
        setCreating(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  const onNewEntry = (): void => {
    setCreating(true);
  };
  const chosen = [...selection.value.values()];

  return (
    <main class="page page-entries" data-testid="entries-page">
      <Toolbar onNewEntry={onNewEntry} searchRef={searchRef} />
      {searching ? <SearchResults onAction={setDialog} /> : <EntryList onAction={setDialog} onNewEntry={onNewEntry} />}
      {creating && (
        <EntryEditor
          mode="create"
          onClose={() => {
            setCreating(false);
          }}
        />
      )}
      {dialog === 'export' && chosen.length > 0 && (
        <ExportDialog
          entries={chosen}
          onClose={() => {
            setDialog(null);
          }}
        />
      )}
      {dialog === 'compile' && chosen.length > 0 && (
        <CompileDialog
          entries={chosen}
          onClose={() => {
            setDialog(null);
          }}
        />
      )}
    </main>
  );
}
