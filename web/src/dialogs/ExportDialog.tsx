/**
 * Export the selected entries. The server renders the export (POST /export);
 * the dialog previews it, and copies it to the clipboard (the main action, for
 * pasting into Word) or downloads it as a file.
 */

import type { JSX } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { isAbort, toAppError, type AppError } from '../api/errors';
import type { EntrySummary, ExportFormat, ExportInclude, ExportResult, SortOrder } from '../api/types';
import { Dialog, DialogHeader } from '../components/Dialog';
import { ErrorNotice } from '../components/ErrorNotice';
import { Icon } from '../components/Icon';
import { copyText } from '../lib/clipboard';
import { formatCount, headline, plural } from '../lib/format';
import { readJsonPref, writePref } from '../lib/prefs';
import { api } from '../state/app';
import { toast } from '../state/toasts';

const FORMATS: readonly { readonly value: ExportFormat; readonly label: string; readonly hint: string }[] = [
  { value: 'text', label: 'Plain text', hint: 'Plain text laid out for pasting into Word: no Markdown symbols' },
  { value: 'markdown', label: 'Markdown', hint: 'Headings and lists in Markdown' },
  { value: 'json', label: 'JSON', hint: 'Structured data for scripts' },
];

const INCLUDE_LABELS: readonly { readonly key: keyof ExportInclude; readonly label: string }[] = [
  { key: 'date', label: 'Date' },
  { key: 'title', label: 'Title' },
  { key: 'summary', label: 'Summary' },
  { key: 'text', label: 'Text' },
  { key: 'tags', label: 'Tags' },
  { key: 'extraction', label: 'Projects, skills and accomplishments' },
];

interface ExportOptions {
  readonly format: ExportFormat;
  readonly include: ExportInclude;
  readonly order: SortOrder;
}

const DEFAULT_OPTIONS: ExportOptions = {
  format: 'text',
  include: { title: true, date: true, tags: true, summary: true, text: true, extraction: false },
  order: 'asc',
};

const OPTIONS_KEY = 'export-options';

function isOptions(value: unknown): value is ExportOptions {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  const include = record['include'];
  return (
    (record['format'] === 'text' || record['format'] === 'markdown' || record['format'] === 'json') &&
    (record['order'] === 'asc' || record['order'] === 'desc') &&
    typeof include === 'object' &&
    include !== null &&
    INCLUDE_LABELS.every(({ key }) => typeof (include as Record<string, unknown>)[key] === 'boolean')
  );
}

interface PreviewState {
  readonly result: ExportResult | null;
  readonly loading: boolean;
  readonly error: AppError | null;
}

export interface ExportDialogProps {
  readonly entries: readonly EntrySummary[];
  readonly onClose: () => void;
}

export function ExportDialog({ entries, onClose }: ExportDialogProps): JSX.Element {
  const [options, setOptions] = useState<ExportOptions>(() => readJsonPref(OPTIONS_KEY, isOptions) ?? DEFAULT_OPTIONS);
  const [preview, setPreview] = useState<PreviewState>({ result: null, loading: true, error: null });
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<AppError | null>(null);
  const copyRef = useRef<HTMLButtonElement>(null);
  const ids = entries.map((entry) => entry.id);
  const idsKey = ids.join(',');
  const nothingIncluded = !Object.values(options.include).some(Boolean);

  useEffect(() => {
    // Remembering the export layout is a convenience; failure only means defaults next time.
    void writePref(OPTIONS_KEY, JSON.stringify(options));
    if (nothingIncluded) {
      setPreview({ result: null, loading: false, error: null });
      return undefined;
    }
    const controller = new AbortController();
    setPreview((p) => ({ ...p, loading: true, error: null }));
    const timer = window.setTimeout(() => {
      api()
        .exportEntries({ entry_ids: ids, format: options.format, include: options.include, order: options.order }, controller.signal)
        .then((result) => {
          setPreview({ result, loading: false, error: null });
        })
        .catch((error: unknown) => {
          if (isAbort(error)) return;
          setPreview({ result: null, loading: false, error: toAppError(error, 'Export') });
        });
    }, 120);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // ids are captured by idsKey.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options, idsKey, nothingIncluded]);

  const update = (patch: Partial<ExportOptions>): void => {
    setOptions((current) => ({ ...current, ...patch }));
    setCopied(false);
  };

  const copy = async (): Promise<void> => {
    if (preview.result === null) return;
    setCopyError(null);
    try {
      await copyText(preview.result.body);
      setCopied(true);
      toast(`Copied ${plural(entries.length, 'entry', 'entries')} to the clipboard`);
    } catch (error) {
      setCopyError(toAppError(error, 'Copy'));
    }
  };

  const download = (): void => {
    if (preview.result === null) return;
    const blob = new Blob([preview.result.body], { type: preview.result.contentType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = preview.result.filename;
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
    }, 10_000);
    toast(`Downloading ${preview.result.filename}`);
  };

  const title = `Export ${plural(entries.length, 'entry', 'entries')}`;
  const ready = preview.result !== null && !preview.loading;

  return (
    <Dialog label={title} onClose={onClose} class="dialog-export" closeOnBackdrop initialFocus={copyRef} testId="export-dialog">
      <div class="dialog-panel">
        <DialogHeader title={title} onClose={onClose} />
        <div class="dialog-body export-body">
          <div class="export-options">
            <fieldset class="fieldset">
              <legend class="field-label">Format</legend>
              <div class="segmented segmented-block" role="group" aria-label="Format">
                {FORMATS.map((format) => (
                  <button
                    type="button"
                    key={format.value}
                    class={options.format === format.value ? 'segment is-on' : 'segment'}
                    aria-pressed={options.format === format.value}
                    title={format.hint}
                    data-testid={`export-format-${format.value}`}
                    onClick={() => {
                      update({ format: format.value });
                    }}
                  >
                    {format.label}
                  </button>
                ))}
              </div>
            </fieldset>
            <fieldset class="fieldset">
              <legend class="field-label">Include</legend>
              <div class="checks">
                {INCLUDE_LABELS.map(({ key, label }) => (
                  <label class="check" key={key}>
                    <input
                      type="checkbox"
                      checked={options.include[key]}
                      onChange={(event) => {
                        update({ include: { ...options.include, [key]: event.currentTarget.checked } });
                      }}
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <fieldset class="fieldset">
              <legend class="field-label">Order</legend>
              <div class="segmented segmented-block" role="group" aria-label="Order">
                <button
                  type="button"
                  class={options.order === 'asc' ? 'segment is-on' : 'segment'}
                  aria-pressed={options.order === 'asc'}
                  onClick={() => {
                    update({ order: 'asc' });
                  }}
                >
                  Oldest first
                </button>
                <button
                  type="button"
                  class={options.order === 'desc' ? 'segment is-on' : 'segment'}
                  aria-pressed={options.order === 'desc'}
                  onClick={() => {
                    update({ order: 'desc' });
                  }}
                >
                  Newest first
                </button>
              </div>
            </fieldset>
            <details class="export-entries">
              <summary>
                Entries <span class="mono muted">{entries.length}</span>
              </summary>
              <ul>
                {[...entries]
                  .sort((a, b) => a.entry_date.localeCompare(b.entry_date))
                  .map((entry) => (
                    <li key={entry.id}>
                      <span class="mono">{entry.entry_date}</span> {headline(entry)}
                    </li>
                  ))}
              </ul>
            </details>
          </div>
          <div class="export-preview">
            <div class="preview-head">
              <span class="field-label">Preview</span>
              {preview.result !== null && (
                <span class="muted mono" data-testid="export-meta">
                  {formatCount(preview.result.body.length)} characters · {preview.result.filename}
                </span>
              )}
              {preview.loading && <span class="busy-dot" role="img" aria-label="Loading" title="Loading" />}
            </div>
            {nothingIncluded ? (
              <p class="empty-note">Choose at least one part to include.</p>
            ) : preview.error !== null ? (
              <ErrorNotice error={preview.error} />
            ) : (
              <textarea
                class={`textarea preview-text${options.format === 'text' ? '' : ' is-code'}${preview.loading ? ' is-stale' : ''}`}
                readOnly
                value={preview.result?.body ?? ''}
                aria-label="Export preview"
                data-testid="export-preview"
              />
            )}
            {copyError !== null && <ErrorNotice error={copyError} compact />}
          </div>
        </div>
        <footer class="dialog-foot">
          <button type="button" class="button" disabled={!ready} onClick={download} data-testid="export-download" title="Save the export as a file">
            <Icon name="download" size={16} />
            <span>Download</span>
          </button>
          <button
            type="button"
            ref={copyRef}
            class="button button-primary"
            disabled={!ready}
            onClick={() => void copy()}
            data-testid="export-copy"
            title="Copy the export, ready to paste into Word"
          >
            <Icon name={copied ? 'check' : 'copy'} size={16} />
            <span>{copied ? 'Copied' : 'Copy to clipboard'}</span>
          </button>
        </footer>
      </div>
    </Dialog>
  );
}
