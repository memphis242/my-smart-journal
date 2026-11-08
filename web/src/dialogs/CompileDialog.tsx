/** Queue a compilation (resume bullets, STAR stories, technical topics, open threads) from the selection. */

import type { JSX } from 'preact';
import { useRef, useState } from 'preact/hooks';
import { ApiError, toAppError, type AppError } from '../api/errors';
import type { EntrySummary } from '../api/types';
import { Dialog, DialogHeader } from '../components/Dialog';
import { ErrorNotice } from '../components/ErrorNotice';
import { plural } from '../lib/format';
import { api, bumpData } from '../state/app';
import { pokeQueue, requestBatch } from '../state/queue';
import { navigate } from '../state/route';
import { clearSelection } from '../state/selection';
import { toast } from '../state/toasts';

const PURPOSES = ['resume update', 'interview prep', 'monthly review', 'performance review'] as const;

/** "2026-10" for one month, else "2026-09-03 to 2026-10-28". */
export function periodFor(entries: readonly EntrySummary[]): string {
  const dates = entries.map((entry) => entry.entry_date).sort();
  const first = dates[0];
  const last = dates.at(-1);
  if (first === undefined || last === undefined) return '';
  if (first.slice(0, 7) === last.slice(0, 7)) return first.slice(0, 7);
  return `${first} to ${last}`;
}

export interface CompileDialogProps {
  readonly entries: readonly EntrySummary[];
  readonly onClose: () => void;
}

export function CompileDialog({ entries, onClose }: CompileDialogProps): JSX.Element {
  const [purpose, setPurpose] = useState<string>(PURPOSES[0]);
  const [period, setPeriod] = useState(() => periodFor(entries));
  const [runNow, setRunNow] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const purposeRef = useRef<HTMLInputElement>(null);
  const valid = purpose.trim() !== '' && period.trim() !== '';

  const submit = async (event: Event): Promise<void> => {
    event.preventDefault();
    if (!valid || pending) return;
    setPending(true);
    setError(null);
    try {
      await api().createCompilation({ entry_ids: entries.map((entry) => entry.id), purpose: purpose.trim(), period: period.trim() });
      let note = 'Compilation queued for the next batch';
      if (runNow) {
        try {
          const requested = await requestBatch();
          note =
            requested.worker?.stale === true
              ? 'Compilation queued, but the worker is not running; start it with msj worker'
              : 'Compilation queued; the worker starts the batch within a few seconds';
        } catch (caught) {
          if (!(caught instanceof ApiError && caught.code === 'BATCH_ALREADY_RUNNING')) throw caught;
          note = 'Compilation queued; it runs after the current batch';
        }
      }
      toast(note, {
        label: 'Open compilations',
        run: () => {
          navigate('compilations');
        },
      });
      clearSelection();
      bumpData();
      pokeQueue();
      onClose();
    } catch (caught) {
      setError(toAppError(caught, 'Queue compilation'));
      setPending(false);
    }
  };

  const title = `Compile ${plural(entries.length, 'entry', 'entries')}`;

  return (
    <Dialog label={title} onClose={onClose} class="dialog-compile" initialFocus={purposeRef} testId="compile-dialog">
      <form class="dialog-panel" onSubmit={(event) => void submit(event)}>
        <DialogHeader title={title} onClose={onClose} />
        <div class="dialog-body">
          <p class="lede">
            Produces resume bullets, STAR stories, technical topics and open threads using only what these entries state.
          </p>
          <div class="field">
            <label class="field-label" for="compile-purpose">
              Purpose
            </label>
            <input
              id="compile-purpose"
              ref={purposeRef}
              class="input"
              value={purpose}
              data-testid="compile-purpose"
              onInput={(event) => {
                setPurpose(event.currentTarget.value);
              }}
            />
            <div class="suggestions" role="group" aria-label="Common purposes">
              {PURPOSES.map((value) => (
                <button
                  type="button"
                  key={value}
                  class={purpose === value ? 'chip chip-button is-on' : 'chip chip-button'}
                  aria-pressed={purpose === value}
                  onClick={() => {
                    setPurpose(value);
                  }}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>
          <div class="field">
            <label class="field-label" for="compile-period">
              Period
            </label>
            <input
              id="compile-period"
              class="input input-mono"
              value={period}
              data-testid="compile-period"
              onInput={(event) => {
                setPeriod(event.currentTarget.value);
              }}
            />
            <p class="field-hint">Defaults to the dates the selected entries span.</p>
          </div>
          <label class="check">
            <input
              type="checkbox"
              checked={runNow}
              onChange={(event) => {
                setRunNow(event.currentTarget.checked);
              }}
            />
            <span>Run the batch now instead of waiting for the worker</span>
          </label>
          {error !== null && <ErrorNotice error={error} />}
        </div>
        <footer class="dialog-foot">
          <button type="button" class="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" class="button button-primary" disabled={!valid || pending} data-testid="compile-submit">
            {pending ? 'Queueing' : 'Queue compilation'}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
