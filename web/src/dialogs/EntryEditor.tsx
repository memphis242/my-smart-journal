/**
 * Add or edit an entry in a modal. New entries are written or recorded; an
 * unsent written draft is kept in this browser so closing the dialog loses
 * nothing. Edits send only the fields that changed.
 */

import type { JSX } from 'preact';
import { useMemo, useRef, useState } from 'preact/hooks';
import { toAppError, type AppError } from '../api/errors';
import type { EntryDetail, EntryPatch } from '../api/types';
import { AudioRecorder, type AudioTake } from '../components/AudioRecorder';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Dialog, DialogHeader } from '../components/Dialog';
import { ErrorNotice } from '../components/ErrorNotice';
import { TagInput } from '../components/TagInput';
import { addDays, isIsoDate, weekday } from '../lib/dates';
import { readEditDraft, readNewDraft, saveEditDraft, saveNewDraft, type Draft } from '../lib/drafts';
import { api, bumpData } from '../state/app';
import { facetState } from '../state/facets';
import { pokeQueue, runBatchNow } from '../state/queue';
import { toast } from '../state/toasts';
import { today } from '../state/view';

type Mode = 'write' | 'record';

export type EntryEditorProps =
  | { readonly mode: 'create'; readonly onClose: () => void }
  | { readonly mode: 'edit'; readonly entry: EntryDetail; readonly onClose: () => void };

export function EntryEditor(props: EntryEditorProps): JSX.Element {
  const editing = props.mode === 'edit' ? props.entry : null;
  const initial = useMemo<{ readonly draft: Draft; readonly restored: boolean }>(() => {
    if (editing !== null) {
      const stored = readEditDraft(editing.id);
      if (stored !== null) return { draft: stored, restored: true };
      return { draft: { text: editing.text, title: editing.title ?? '', tags: editing.tags, date: editing.entry_date }, restored: false };
    }
    const stored = readNewDraft();
    return stored === null ? { draft: { text: '', title: '', tags: [], date: today.value }, restored: false } : { draft: stored, restored: stored.text !== '' };
  }, [editing]);
  const restored = initial.restored;

  const [mode, setMode] = useState<Mode>('write');
  const [text, setText] = useState(initial.draft.text);
  const [title, setTitle] = useState(initial.draft.title);
  const [tags, setTags] = useState<readonly string[]>(initial.draft.tags);
  const [date, setDate] = useState(initial.draft.date);
  const [take, setTake] = useState<AudioTake | null>(null);
  const [recording, setRecording] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const suggestions = facetState.value.facets?.tags.map((facet) => facet.value) ?? [];

  const dateValid = isIsoDate(date);
  const textChanged = editing !== null && text.trim() !== editing.text.trim();
  const changes: EntryPatch = useMemo(() => {
    if (editing === null) return {};
    const patch: { -readonly [K in keyof EntryPatch]: EntryPatch[K] } = {};
    const newTitle = title.trim() === '' ? null : title.trim();
    if (newTitle !== editing.title) patch.title = newTitle;
    if (tags.join('\u0000') !== editing.tags.join('\u0000')) patch.tags = tags;
    if (date !== editing.entry_date) patch.entry_date = date;
    if (text.trim() !== editing.text.trim()) patch.text = text.trim();
    return patch;
  }, [editing, title, tags, date, text]);
  const dirty = editing !== null ? Object.keys(changes).length > 0 : false;

  const canSubmit =
    !pending &&
    dateValid &&
    !recording &&
    (editing !== null ? dirty && (changes.text === undefined || changes.text !== '') : mode === 'write' ? text.trim() !== '' : take !== null);

  const remember = (next: Partial<Draft>): void => {
    const draft = { text, title, tags, date, ...next };
    if (editing === null) {
      saveNewDraft(draft);
      return;
    }
    const unchanged =
      draft.text.trim() === editing.text.trim() &&
      draft.title.trim() === (editing.title ?? '') &&
      draft.date === editing.entry_date &&
      draft.tags.join('\u0000') === editing.tags.join('\u0000');
    saveEditDraft(unchanged ? null : { ...draft, id: editing.id, revision: editing.revision });
  };

  const close = (): void => {
    if (pending) return;
    if (recording || (editing !== null && dirty) || (editing === null && mode === 'record' && take !== null)) {
      setConfirmDiscard(true);
      return;
    }
    if (editing !== null) saveEditDraft(null);
    props.onClose();
  };

  const submit = async (event: Event): Promise<void> => {
    event.preventDefault();
    if (!canSubmit) return;
    setPending(true);
    setError(null);
    const titleValue = title.trim() === '' ? null : title.trim();
    try {
      if (editing !== null) {
        await api().patchEntry(editing.id, changes);
        saveEditDraft(null);
        toast(changes.text !== undefined ? 'Saved; the new text is queued for processing' : 'Saved');
      } else if (mode === 'write') {
        await api().createTextEntry({ text: text.trim(), title: titleValue, tags, entry_date: date });
        saveNewDraft(null);
        toast('Entry added to the queue', { label: 'Run batch now', run: () => void runBatchNow() });
      } else if (take !== null) {
        await api().createAudioEntry({ audio: take.blob, mimeType: take.mimeType, fileName: take.fileName, title: titleValue, tags, entry_date: date });
        toast('Recording uploaded and queued', { label: 'Run batch now', run: () => void runBatchNow() });
      }
      bumpData();
      pokeQueue();
      props.onClose();
    } catch (caught) {
      setError(toAppError(caught, editing !== null ? 'Save entry' : mode === 'write' ? 'Add entry' : 'Upload recording'));
      setPending(false);
    }
  };

  const heading = editing !== null ? 'Edit entry' : 'New entry';

  return (
    <Dialog label={heading} onClose={close} class="dialog-editor" initialFocus={mode === 'write' ? textRef : undefined} testId="entry-editor">
      <form class="dialog-panel" onSubmit={(event) => void submit(event)}>
        <DialogHeader title={heading} onClose={close}>
          {editing === null && (
            <div class="segmented segmented-small" role="group" aria-label="Entry type">
              <button
                type="button"
                class={mode === 'write' ? 'segment is-on' : 'segment'}
                aria-pressed={mode === 'write'}
                disabled={recording}
                title="Type the entry"
                data-testid="mode-write"
                onClick={() => {
                  setMode('write');
                }}
              >
                Write
              </button>
              <button
                type="button"
                class={mode === 'record' ? 'segment is-on' : 'segment'}
                aria-pressed={mode === 'record'}
                title="Record the entry with the microphone, or upload a recording"
                data-testid="mode-record"
                onClick={() => {
                  setMode('record');
                }}
              >
                Record
              </button>
            </div>
          )}
        </DialogHeader>
        <div class="dialog-body editor-body">
          <div class="editor-meta">
            <div class="field field-date">
              <label class="field-label" for="entry-date">
                Date
              </label>
              <div class="date-field">
                <input
                  id="entry-date"
                  class={dateValid ? 'input input-mono' : 'input input-mono is-invalid'}
                  value={date}
                  inputMode="numeric"
                  placeholder="YYYY-MM-DD"
                  title="YYYY-MM-DD; Up and Down arrows change the day"
                  data-testid="entry-date"
                  onInput={(event) => {
                    const value = event.currentTarget.value.trim();
                    setDate(value);
                    remember({ date: value });
                  }}
                  onKeyDown={(event) => {
                    if ((event.key === 'ArrowUp' || event.key === 'ArrowDown') && dateValid) {
                      event.preventDefault();
                      const next = addDays(date, event.key === 'ArrowUp' ? 1 : -1);
                      setDate(next);
                      remember({ date: next });
                    }
                  }}
                />
                <span class="field-suffix mono muted">{dateValid ? weekday(date) : ''}</span>
              </div>
              {!dateValid && <p class="field-hint is-invalid">Write the date as YYYY-MM-DD.</p>}
            </div>
            <div class="field field-title">
              <label class="field-label" for="entry-title">
                Title <span class="muted">(optional)</span>
              </label>
              <input
                id="entry-title"
                class="input"
                value={title}
                maxLength={200}
                data-testid="entry-title"
                onInput={(event) => {
                  setTitle(event.currentTarget.value);
                  remember({ title: event.currentTarget.value });
                }}
              />
            </div>
          </div>
          <div class="field">
            <label class="field-label" for="entry-tags">
              Tags
            </label>
            <TagInput
              id="entry-tags"
              tags={tags}
              suggestions={suggestions}
              onChange={(next) => {
                setTags(next);
                remember({ tags: next });
              }}
            />
          </div>
          {mode === 'write' || editing !== null ? (
            <div class="field field-grow">
              <label class="field-label" for="entry-text">
                {editing?.source === 'audio' ? 'Transcript' : 'Text'}
              </label>
              <textarea
                id="entry-text"
                ref={textRef}
                class="textarea"
                value={text}
                rows={12}
                placeholder="What happened, what you did, what is still open."
                data-testid="entry-text-input"
                onInput={(event) => {
                  setText(event.currentTarget.value);
                  remember({ text: event.currentTarget.value });
                }}
              />
              <p class="field-hint">
                {editing !== null
                  ? restored
                    ? 'Restored your unsaved edits from this browser.'
                    : textChanged
                      ? 'Changed text is cleaned up, summarized and extracted again in the next batch.'
                      : 'Date, title and tag changes apply without reprocessing.'
                  : restored
                    ? 'Restored your unsent draft from this browser.'
                    : 'Your draft is kept in this browser until you add it.'}
              </p>
            </div>
          ) : (
            <div class="field field-grow">
              <span class="field-label">Recording</span>
              <AudioRecorder onTake={setTake} onRecordingChange={setRecording} />
              <p class="field-hint">The recording is transcribed in the next batch, then cleaned up like typed text.</p>
            </div>
          )}
          {error !== null && <ErrorNotice error={error} />}
        </div>
        <footer class="dialog-foot">
          <button type="button" class="button" onClick={close}>
            Cancel
          </button>
          <button type="submit" class="button button-primary" disabled={!canSubmit} data-testid="entry-submit">
            {pending
              ? editing !== null
                ? 'Saving'
                : 'Adding'
              : editing !== null
                ? 'Save changes'
                : mode === 'write'
                  ? 'Add entry'
                  : 'Upload recording'}
          </button>
        </footer>
      </form>
      {confirmDiscard && (
        <ConfirmDialog
          title={recording ? 'Stop and discard the recording?' : editing !== null ? 'Discard your changes?' : 'Discard the recording?'}
          action="Discard"
          destructive
          onCancel={() => {
            setConfirmDiscard(false);
          }}
          onConfirm={() => {
            setConfirmDiscard(false);
            if (editing !== null) saveEditDraft(null);
            props.onClose();
          }}
        >
          <p>
            {editing !== null
              ? 'The entry stays as it was; the edits in this dialog are lost.'
              : 'The audio has not been uploaded and will be lost.'}
          </p>
        </ConfirmDialog>
      )}
    </Dialog>
  );
}
