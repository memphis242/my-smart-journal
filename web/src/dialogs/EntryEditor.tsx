/**
 * Add or edit an entry in a modal. New entries are written or recorded; an
 * unsent written draft is kept in this browser so closing the dialog loses
 * nothing. Edits send only the fields that changed, with the version they
 * started from; if the entry changed meanwhile, the server refuses
 * (ENTRY_CHANGED) and the editor offers to reapply the user's changes on top
 * of the current entry.
 */

import type { JSX } from 'preact';
import { useMemo, useRef, useState } from 'preact/hooks';
import { ApiError, toAppError, type AppError } from '../api/errors';
import type { EntryDetail, EntryPatch } from '../api/types';
import { AudioRecorder, type AudioTake } from '../components/AudioRecorder';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Dialog, DialogHeader } from '../components/Dialog';
import { ErrorNotice } from '../components/ErrorNotice';
import { StatusMarker } from '../components/StatusMarker';
import { TagInput } from '../components/TagInput';
import { addDays, formatDateTime, isIsoDate, weekday } from '../lib/dates';
import {
  applyChanges,
  changedFields,
  differingFields,
  dropEditDraft,
  readEditDraft,
  readNewDraft,
  reapply,
  sameBase,
  saveEditDraft,
  saveNewDraft,
  type DraftBase,
  type DraftFields,
  type EditDraft,
  type FieldName,
} from '../lib/drafts';
import { api, bumpData } from '../state/app';
import { facetState } from '../state/facets';
import { pokeQueue, runBatchNow } from '../state/queue';
import { toast } from '../state/toasts';
import { today } from '../state/view';

type Mode = 'write' | 'record';

export type EntryEditorProps =
  | { readonly mode: 'create'; readonly onClose: () => void }
  | { readonly mode: 'edit'; readonly entry: EntryDetail; readonly onClose: () => void };

function fieldsOf(entry: EntryDetail): DraftFields {
  return { text: entry.text, title: entry.title ?? '', tags: entry.tags, date: entry.entry_date };
}

function baseOf(entry: EntryDetail): DraftBase {
  return { ...fieldsOf(entry), version: entry.version, revision: entry.revision };
}

/** "title and text", "date, tags and text". */
function listFields(names: readonly FieldName[]): string {
  if (names.length === 0) return 'nothing you can see';
  if (names.length === 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} and ${names.at(-1) ?? ''}`;
}

interface Opening {
  readonly values: DraftFields;
  readonly base: DraftBase | null;
  readonly restored: boolean;
  /** A draft started from an older version of the entry: the user decides. */
  readonly offer: EditDraft | null;
}

interface Conflict {
  readonly current: EntryDetail;
  /** Fields that changed elsewhere since editing began. */
  readonly changed: readonly FieldName[];
}

export function EntryEditor(props: EntryEditorProps): JSX.Element {
  const editing = props.mode === 'edit' ? props.entry : null;
  const opening = useMemo<Opening>(() => {
    if (editing !== null) {
      const base = baseOf(editing);
      const draft = readEditDraft(editing.id);
      if (draft === null) return { values: fieldsOf(editing), base, restored: false, offer: null };
      if (sameBase(draft.base, base)) return { values: applyChanges(fieldsOf(editing), draft.changes), base, restored: true, offer: null };
      return { values: fieldsOf(editing), base, restored: false, offer: draft };
    }
    const stored = readNewDraft();
    return stored === null
      ? { values: { text: '', title: '', tags: [], date: today.value }, base: null, restored: false, offer: null }
      : { values: stored, base: null, restored: stored.text !== '', offer: null };
  }, [editing]);

  const [mode, setMode] = useState<Mode>('write');
  const [text, setText] = useState(opening.values.text);
  const [title, setTitle] = useState(opening.values.title);
  const [tags, setTags] = useState<readonly string[]>(opening.values.tags);
  const [date, setDate] = useState(opening.values.date);
  const [base, setBase] = useState<DraftBase | null>(opening.base);
  const [restored, setRestored] = useState(opening.restored);
  const [offer, setOffer] = useState<EditDraft | null>(opening.offer);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [storageFailed, setStorageFailed] = useState(false);
  const [take, setTake] = useState<AudioTake | null>(null);
  const [recording, setRecording] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const suggestions = facetState.value.facets?.tags.map((facet) => facet.value) ?? [];

  const values: DraftFields = { text, title, tags, date };
  const dateValid = isIsoDate(date);
  const changes = editing !== null && base !== null ? changedFields(base, values) : {};
  const dirty = Object.keys(changes).length > 0;
  const textChanged = changes.text !== undefined;

  const canSubmit =
    !pending &&
    dateValid &&
    !recording &&
    conflict === null &&
    (editing !== null ? dirty && changes.text?.trim() !== '' : mode === 'write' ? text.trim() !== '' : take !== null);

  const setValues = (next: DraftFields): void => {
    setText(next.text);
    setTitle(next.title);
    setTags(next.tags);
    setDate(next.date);
  };

  const remember = (next: Partial<DraftFields>, against: DraftBase | null = base): void => {
    const all = { ...values, ...next };
    if (editing === null) {
      setStorageFailed(!saveNewDraft(all));
      return;
    }
    // While an older draft waits for the user's decision, it stays as it is.
    if (offer !== null || against === null) return;
    setStorageFailed(!saveEditDraft(editing.id, against, all));
  };

  const applyOffer = (): void => {
    if (offer === null || base === null || editing === null) return;
    const next = applyChanges(fieldsOf(editing), offer.changes);
    setValues(next);
    setOffer(null);
    setRestored(true);
    setStorageFailed(!saveEditDraft(editing.id, base, next));
  };

  const discardOffer = (): void => {
    if (editing !== null) dropEditDraft(editing.id);
    setOffer(null);
  };

  const reloadAndReapply = (): void => {
    if (conflict === null || base === null || editing === null) return;
    const nextBase = baseOf(conflict.current);
    const next = reapply(base, fieldsOf(conflict.current), values);
    setValues(next);
    setBase(nextBase);
    setConflict(null);
    setStorageFailed(!saveEditDraft(editing.id, nextBase, next));
  };

  const close = (): void => {
    if (pending) return;
    if (recording || (editing !== null && dirty) || (editing === null && mode === 'record' && take !== null)) {
      setConfirmDiscard(true);
      return;
    }
    // Nothing changed here; an older draft still waiting for a decision is kept.
    if (editing !== null && offer === null) dropEditDraft(editing.id);
    props.onClose();
  };

  const saveEdit = async (entry: EntryDetail, from: DraftBase): Promise<void> => {
    const patch: { -readonly [K in keyof EntryPatch]: EntryPatch[K] } = {};
    if (changes.title !== undefined) patch.title = changes.title.trim() === '' ? null : changes.title.trim();
    if (changes.tags !== undefined) patch.tags = changes.tags;
    if (changes.date !== undefined) patch.entry_date = changes.date;
    if (changes.text !== undefined) patch.text = changes.text.trim();
    if (from.version !== null) patch.expected_version = from.version;
    try {
      await api().patchEntry(entry.id, patch);
    } catch (caught) {
      if (!(caught instanceof ApiError && caught.code === 'ENTRY_CHANGED')) throw caught;
      // Someone else saved first: keep the editor and its text, and show what changed.
      const current = await api().getEntry(entry.id);
      setConflict({ current, changed: differingFields(from, fieldsOf(current)) });
      return;
    }
    dropEditDraft(entry.id);
    toast(patch.text !== undefined ? 'Saved; the new text is queued for processing' : 'Saved');
    bumpData();
    props.onClose();
  };

  const submit = async (event: Event): Promise<void> => {
    event.preventDefault();
    if (!canSubmit) return;
    setPending(true);
    setError(null);
    const titleValue = title.trim() === '' ? null : title.trim();
    try {
      if (editing !== null && base !== null) {
        await saveEdit(editing, base);
        setPending(false);
        return;
      }
      if (mode === 'write') {
        await api().createTextEntry({ text: text.trim(), title: titleValue, tags, entry_date: date });
        void saveNewDraft(null);
        toast('Entry added to the queue', { label: 'Run batch now', run: () => void runBatchNow() });
      } else if (take !== null) {
        await api().createAudioEntry({ audio: take.blob, mimeType: take.mimeType, fileName: take.fileName, title: titleValue, tags, entry_date: date });
        // The title, tags and date belonged to this recording; written text not yet added stays as the draft.
        void saveNewDraft({ text, title: '', tags: [], date: today.value });
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
          {offer !== null && base !== null && (
            <div class="notice notice-attention" data-testid="draft-conflict">
              <StatusMarker kind="attention" label="Draft from an older version" decorative />
              <div class="notice-main">
                <p class="notice-message">
                  <strong>This entry changed since your unsaved edits</strong> from{' '}
                  <span class="mono">{formatDateTime(new Date(offer.savedAt).toISOString())}</span>: its{' '}
                  {listFields(differingFields(offer.base, base))} changed. Your edits touched its {listFields(Object.keys(offer.changes) as FieldName[])}.
                </p>
                <p class="notice-hint">Apply your edits on top of the current entry, or discard them.</p>
                <div class="notice-buttons">
                  <button type="button" class="button button-small" onClick={applyOffer} data-testid="apply-draft">
                    Apply my edits
                  </button>
                  <button type="button" class="button button-small button-quiet" onClick={discardOffer} data-testid="discard-draft">
                    Discard them
                  </button>
                </div>
              </div>
            </div>
          )}
          {conflict !== null && (
            <div class="notice notice-attention" data-testid="entry-changed">
              <StatusMarker kind="attention" label="Changed elsewhere" decorative />
              <div class="notice-main">
                <p class="notice-message">
                  <strong>This entry changed while you were editing.</strong> Its {listFields(conflict.changed)} changed
                  {base?.version != null && conflict.current.version !== null && (
                    <span class="muted mono">
                      {' '}
                      (version {base.version} to {conflict.current.version})
                    </span>
                  )}
                  . Nothing was saved. <code class="error-code">ENTRY_CHANGED</code>
                </p>
                <p class="notice-hint">Your changes stay as typed; fields you did not touch take the new values.</p>
                <div class="notice-buttons">
                  <button type="button" class="button button-small button-primary" onClick={reloadAndReapply} data-testid="reapply">
                    Reload and reapply my changes
                  </button>
                </div>
              </div>
            </div>
          )}
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
              <p class={storageFailed ? 'field-hint is-invalid' : 'field-hint'} data-testid="draft-hint">
                {storageFailed
                  ? editing !== null
                    ? 'This browser could not keep your changes (storage is unavailable or full). Save them before closing.'
                    : 'This browser could not keep the draft (storage is unavailable or full). Add the entry before closing.'
                  : editing !== null
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
            if (editing !== null) dropEditDraft(editing.id);
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
