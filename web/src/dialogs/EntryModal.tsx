/**
 * Entry detail in a modal. Tall bars on the left and right edges step to the
 * next older and next newer entry; Left/Right arrow keys and a horizontal
 * swipe do the same. Order comes from the list the entry was opened from;
 * past its loaded rows, GET /entries/{id}/neighbors fills in.
 */

import type { JSX } from 'preact';
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { isAbort, toAppError, type AppError } from '../api/errors';
import { extraction as extractionGuard } from '../api/guards';
import type { EntryDetail, EntrySummary, Extraction, Job } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Dialog } from '../components/Dialog';
import { ErrorNotice } from '../components/ErrorNotice';
import { Icon } from '../components/Icon';
import { StatusMarker, entryMarker, jobMarker } from '../components/StatusMarker';
import { WIDE, useMediaQuery } from '../hooks/useMediaQuery';
import { copyText } from '../lib/clipboard';
import { formatDateTime, formatTime, weekday } from '../lib/dates';
import { ENTRY_STATUS_HINTS, ENTRY_STATUS_LABELS, JOB_STATUS_LABELS, paragraphs, stageLabel } from '../lib/format';
import { copyEntry } from '../pages/entries/EntryRows';
import { api, bumpData, dataVersion } from '../state/app';
import { cachedDetail, closeEntry, moveEntry, openEntry, peekDetail, storeDetail, type NavContext, type OpenEntry } from '../state/entry-modal';
import { pokeQueue } from '../state/queue';
import { forgetSelected } from '../state/selection';
import { toast } from '../state/toasts';
import { EntryEditor } from './EntryEditor';

interface NeighborState {
  readonly newer: string | null;
  readonly older: string | null;
  readonly loading: boolean;
  readonly error: AppError | null;
}

function fromList(id: string, context: NavContext): { newer: string | null; older: string | null; index: number } {
  const index = context.newestFirst.indexOf(id);
  if (index < 0) return { newer: null, older: null, index };
  return {
    newer: index > 0 ? (context.newestFirst[index - 1] ?? null) : null,
    older: context.newestFirst[index + 1] ?? null,
    index,
  };
}

function useNeighbors(id: string, context: NavContext): NeighborState {
  const local = fromList(id, context);
  const needServer = !context.complete && (local.index < 0 || local.newer === null || local.older === null);
  const [server, setServer] = useState<NeighborState | null>(null);

  useEffect(() => {
    setServer(null);
    if (!needServer) return undefined;
    const controller = new AbortController();
    setServer({ newer: null, older: null, loading: true, error: null });
    api()
      .neighbors(id, context.filters, controller.signal)
      .then((result) => {
        setServer({ ...result, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (isAbort(error)) return;
        setServer({ newer: null, older: null, loading: false, error: toAppError(error, 'Find the next entry') });
      });
    return () => {
      controller.abort();
    };
  }, [id, context, needServer]);

  return {
    newer: local.newer ?? server?.newer ?? null,
    older: local.older ?? server?.older ?? null,
    loading: server?.loading ?? false,
    error: server?.error ?? null,
  };
}

function parseExtraction(detail: EntryDetail | null): Extraction | null {
  const artifact = detail?.artifacts.find((a) => a.kind === 'extraction') ?? null;
  if (artifact === null) return null;
  try {
    return extractionGuard(JSON.parse(artifact.content) as unknown, 'extraction');
  } catch {
    return null;
  }
}

function originalText(detail: EntryDetail | null): { readonly label: string; readonly text: string } | null {
  if (detail === null) return null;
  const transcript = detail.artifacts.find((a) => a.kind === 'raw_transcript');
  if (transcript !== undefined) return { label: 'Raw transcript', text: transcript.content };
  const typed = detail.artifacts.find((a) => a.kind === 'user_text');
  return typed === undefined ? null : { label: 'As written', text: typed.content };
}

function JobLine({ job, onRetried }: { readonly job: Job; readonly onRetried: () => void }): JSX.Element {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const blocked = job.blocked_by.length > 0;
  const status = blocked && job.status === 'queued' ? JOB_STATUS_LABELS.blocked : JOB_STATUS_LABELS[job.status];
  const label = stageLabel(job.kind, job.target, job.turn);
  return (
    <li class={`job-row job-${job.status}`} data-testid="entry-job" data-status={job.status}>
      <div class="job-line">
        <StatusMarker kind={jobMarker(job.status, blocked)} label={status} decorative />
        <span class="job-stage">{label}</span>
        <span class="job-status" title={blocked ? `Waiting on failed ${job.blocked_by.map((b) => stageLabel(b.kind, null, null).toLowerCase()).join(', ')}` : undefined}>
          {status}
        </span>
        {job.finished_at !== null && job.status === 'done' && <span class="job-wait mono">{formatTime(job.finished_at)}</span>}
        {job.attempts > 1 && <span class="job-wait mono" title="Attempts used">x{job.attempts}</span>}
        {job.status === 'failed' && (
          <button
            type="button"
            class="button button-small job-retry"
            disabled={pending}
            title="Reset this job's attempts and queue it for the next batch"
            data-testid="entry-retry-job"
            onClick={() => {
              setPending(true);
              setError(null);
              api()
                .retryJob(job.id)
                .then(() => {
                  toast(`${label} queued again`);
                  onRetried();
                })
                .catch((caught: unknown) => {
                  setError(toAppError(caught, 'Retry job'));
                })
                .finally(() => {
                  setPending(false);
                });
            }}
          >
            <Icon name="retry" size={14} />
            <span>{pending ? 'Retrying' : 'Retry'}</span>
          </button>
        )}
      </div>
      {job.last_error !== null && (
        <details class="job-error" open={job.status === 'failed'}>
          <summary>{job.status === 'failed' ? 'Error' : 'Last error'}</summary>
          <p class="job-error-text">{job.last_error}</p>
        </details>
      )}
      {error !== null && <ErrorNotice error={error} compact />}
    </li>
  );
}

function Section({ title, children, tools }: { readonly title: string; readonly children: JSX.Element | JSX.Element[]; readonly tools?: JSX.Element | undefined }): JSX.Element {
  return (
    <section class="entry-section">
      <header class="entry-section-head">
        <h3 class="section-label">{title}</h3>
        {tools}
      </header>
      {children}
    </section>
  );
}

function LabelRows({ entry }: { readonly entry: EntrySummary }): JSX.Element | null {
  const rows = [
    { label: 'Projects', values: entry.projects, cls: 'chip chip-project' },
    { label: 'Skills', values: entry.skills, cls: 'chip' },
    { label: 'Tags', values: entry.tags, cls: 'chip chip-tag' },
  ].filter((row) => row.values.length > 0);
  if (rows.length === 0) return null;
  return (
    <dl class="label-rows">
      {rows.map((row) => (
        <div class="label-row" key={row.label}>
          <dt>{row.label}</dt>
          <dd>
            {row.values.map((value) => (
              <span class={row.cls} key={value}>
                {value}
              </span>
            ))}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function EntryBody({
  entry,
  detail,
  onChanged,
  onCopyError,
}: {
  readonly entry: EntrySummary;
  readonly detail: EntryDetail | null;
  readonly onChanged: () => void;
  readonly onCopyError: (error: AppError | null) => void;
}): JSX.Element {
  const extraction = parseExtraction(detail);
  const original = originalText(detail);
  const jobs = detail?.jobs ?? [];
  const failed = jobs.filter((job) => job.status === 'failed');
  const sessions = detail === null ? [] : Object.entries(detail.review_sessions).filter(([, s]) => s.status === 'needs_attention');
  const textParagraphs = paragraphs(entry.text);
  const pendingAudio = entry.source === 'audio' && textParagraphs.length === 0;

  return (
    <div class="entry-body">
      {entry.status === 'needs_attention' && (
        <div class="notice notice-attention" data-testid="attention-notice">
          <StatusMarker kind="attention" label="Needs attention" decorative />
          <div class="notice-main">
            <p class="notice-message">
              <strong>Needs attention.</strong> The fact check still found claims the source does not support after its last review turn.
            </p>
            {sessions.map(([target, session]) => (
              <div class="claims" key={target}>
                <p class="claims-head">{stageLabel('review', target, null).replace('Review ', '')}: {session.turns_used} review turns</p>
                <ul class="claims-list">
                  {session.remaining_claims.map((claim, index) => (
                    <li key={index}>{claim}</li>
                  ))}
                </ul>
              </div>
            ))}
            <p class="notice-hint">Edit the entry text to correct it; saving queues a fresh review.</p>
          </div>
        </div>
      )}
      {failed.length > 0 && (
        <div class="notice notice-error" data-testid="failed-notice">
          <StatusMarker kind="failed" label="Failed" decorative />
          <div class="notice-main">
            <p class="notice-message">
              <strong>Processing failed</strong> at {failed.map((job) => stageLabel(job.kind, job.target, job.turn).toLowerCase()).join(', ')}. The steps after it wait until it is retried.
            </p>
            <p class="notice-hint">Fix the cause named in the error below, then retry the step.</p>
          </div>
        </div>
      )}
      {(entry.status === 'queued' || entry.status === 'processing') && failed.length === 0 && (
        <p class="inline-note" data-testid="pending-note">
          <StatusMarker kind={entryMarker(entry.status)} label={ENTRY_STATUS_LABELS[entry.status]} decorative />
          <span>
            {entry.status === 'queued' ? 'Queued for the next batch.' : 'Being processed now.'} The summary and extraction appear when processing finishes.
          </span>
        </p>
      )}

      {entry.summary !== null && entry.summary.trim() !== '' && (
        <Section title="Summary">
          <p class="prose summary-text">{entry.summary}</p>
        </Section>
      )}

      <Section
        title={entry.source === 'audio' ? 'Transcript' : 'Entry'}
        tools={
          textParagraphs.length > 0 ? (
            <button
              type="button"
              class="icon-button icon-button-small"
              title="Copy only the entry text"
              aria-label="Copy entry text"
              onClick={() => {
                copyText(entry.text.trim())
                  .then(() => {
                    onCopyError(null);
                    toast('Copied the entry text');
                  })
                  .catch((error: unknown) => {
                    onCopyError(toAppError(error, 'Copy'));
                  });
              }}
            >
              <Icon name="copy" size={16} />
            </button>
          ) : undefined
        }
      >
        {pendingAudio ? (
          <p class="muted">
            {entry.status === 'failed'
              ? 'No transcript yet: transcription failed. The error and a retry are under Processing below.'
              : entry.status === 'processing'
                ? 'The recording is being transcribed; the text appears here when it is done.'
                : 'The recording is transcribed in the next batch; the text appears here then.'}
          </p>
        ) : (
          <div class="prose entry-text" data-testid="entry-text">
            {textParagraphs.map((paragraph, index) => (
              <p key={index}>{paragraph}</p>
            ))}
          </div>
        )}
      </Section>

      <LabelRows entry={entry} />

      {extraction !== null && extraction.accomplishments.length > 0 && (
        <Section title="Accomplishments">
          <ul class="accomplishments">
            {extraction.accomplishments.map((item, index) => (
              <li key={index}>
                <span>{item.description}</span>
                {item.metrics.length > 0 && (
                  <span class="metrics">
                    {item.metrics.map((metric) => (
                      <span class="metric mono" key={metric}>
                        {metric}
                      </span>
                    ))}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {extraction !== null && (extraction.people_roles.length > 0 || extraction.star_fragments.length > 0) && (
        <details class="entry-more">
          <summary>
            People and STAR fragments
            <span class="muted"> {extraction.people_roles.length + extraction.star_fragments.length}</span>
          </summary>
          {extraction.people_roles.length > 0 && (
            <dl class="people">
              {extraction.people_roles.map((person) => (
                <div class="person" key={person.name}>
                  <dt>{person.name}</dt>
                  <dd>{person.role}</dd>
                </div>
              ))}
            </dl>
          )}
          {extraction.star_fragments.map((star, index) => (
            <dl class="star" key={index}>
              <div>
                <dt>Situation</dt>
                <dd>{star.situation}</dd>
              </div>
              <div>
                <dt>Task</dt>
                <dd>{star.task}</dd>
              </div>
              <div>
                <dt>Action</dt>
                <dd>{star.action}</dd>
              </div>
              <div>
                <dt>Result</dt>
                <dd>{star.result}</dd>
              </div>
            </dl>
          ))}
        </details>
      )}

      {original !== null && original.text.trim() !== entry.text.trim() && (
        <details class="entry-more">
          <summary>{original.label}</summary>
          <div class="prose original-text">
            {paragraphs(original.text).map((p, index) => (
              <p key={index}>{p}</p>
            ))}
          </div>
        </details>
      )}

      {jobs.length > 0 && (
        <details class="entry-more" open={failed.length > 0} data-testid="processing-details">
          <summary>
            Processing
            <span class="muted">
              {' '}
              <span class="mono">{jobs.filter((j) => j.status === 'done').length}</span> of <span class="mono">{jobs.length}</span> steps done
            </span>
          </summary>
          <ul class="job-list">
            {jobs.map((job) => (
              <JobLine
                key={job.id}
                job={job}
                onRetried={() => {
                  onChanged();
                  pokeQueue();
                }}
              />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function NavBar({ side, target, loading, onGo }: { readonly side: 'older' | 'newer'; readonly target: string | null; readonly loading: boolean; readonly onGo: () => void }): JSX.Element {
  const busy = target === null && loading;
  const label = side === 'older' ? 'Older entry' : 'Newer entry';
  const key = side === 'older' ? 'Left arrow' : 'Right arrow';
  const title = target !== null ? `${label} (${key})` : loading ? `Looking for the ${side} entry` : side === 'older' ? 'No older entry' : 'No newer entry';
  return (
    <button
      type="button"
      class={busy ? `nav-bar nav-${side} is-busy` : `nav-bar nav-${side}`}
      disabled={target === null}
      aria-busy={busy}
      title={title}
      aria-label={label}
      data-testid={`nav-${side}`}
      onClick={onGo}
    >
      <Icon name={side === 'older' ? 'chevron-left' : 'chevron-right'} size={28} />
    </button>
  );
}

function EntryModalInner({ open }: { readonly open: OpenEntry }): JSX.Element {
  const { id, context, direction } = open;
  const version = dataVersion.value;
  const wide = useMediaQuery(WIDE);
  const preview = context.previews.get(id) ?? null;
  const [loaded, setLoaded] = useState<EntryDetail | null>(() => peekDetail(id));
  const [error, setError] = useState<AppError | null>(null);
  const [copyError, setCopyError] = useState<AppError | null>(null);
  /** The editor works on a snapshot taken when it opened, so refreshes never reset or close it. */
  const [editing, setEditing] = useState<EntryDetail | null>(null);
  /** Deletion confirms against the loaded detail, which carries the compilation count. */
  const [deleting, setDeleting] = useState<EntryDetail | null>(null);
  const neighbors = useNeighbors(id, context);
  const swipe = useRef<{ readonly x: number; readonly y: number; readonly id: number } | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const sheetRef = useRef<HTMLElement>(null);
  // Never show another entry's detail, even for the one render after stepping.
  const detail = loaded !== null && loaded.id === id ? loaded : null;

  // Stepping to another entry: show what is known about it and start at the top.
  useLayoutEffect(() => {
    setLoaded(peekDetail(id));
    setError(null);
    setCopyError(null);
    bodyRef.current?.scrollTo({ top: 0 });
  }, [id]);

  // Load, and reload when entries change; the shown detail and the reading position stay until the new one arrives.
  useEffect(() => {
    const controller = new AbortController();
    api()
      .getEntry(id, controller.signal)
      .then((fresh) => {
        storeDetail(fresh, version);
        setLoaded(fresh);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (isAbort(caught)) return;
        setError(toAppError(caught, 'Load entry'));
      });
    return () => {
      controller.abort();
    };
  }, [id, version]);

  // Warm the cache for the entries the bars lead to.
  useEffect(() => {
    for (const neighbor of [neighbors.newer, neighbors.older]) {
      if (neighbor === null || cachedDetail(neighbor, version) !== null) continue;
      api()
        .getEntry(neighbor)
        .then((loaded) => {
          storeDetail(loaded, version);
        })
        .catch(() => {
          // A failed prefetch is retried, and reported, when the user steps there.
        });
    }
  }, [neighbors.newer, neighbors.older, version]);

  const entry: EntrySummary | null = detail ?? preview;
  const go = (step: -1 | 1): void => {
    const target = step === 1 ? neighbors.newer : neighbors.older;
    if (target !== null) moveEntry(target, step);
  };

  const close = (): void => {
    closeEntry();
    window.setTimeout(() => {
      const row = document.querySelector<HTMLElement>(`[data-row-id="${CSS.escape(id)}"]`);
      row?.focus({ preventScroll: true });
    }, 0);
  };

  const onKeyDown = (event: KeyboardEvent): void => {
    if (editing !== null || deleting !== null) return;
    const target = event.target as HTMLElement;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable) return;
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      go(-1);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      go(1);
    }
  };

  const onPointerDown = (event: PointerEvent): void => {
    swipe.current = event.pointerType === 'mouse' ? null : { x: event.clientX, y: event.clientY, id: event.pointerId };
  };
  const onPointerUp = (event: PointerEvent): void => {
    const start = swipe.current;
    swipe.current = null;
    if (start?.id !== event.pointerId) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    // A deliberate horizontal swipe: finger moving left brings the newer entry in from the right.
    if (Math.abs(dx) > 64 && Math.abs(dx) > 2 * Math.abs(dy)) go(dx < 0 ? 1 : -1);
  };

  const remove = async (target: EntryDetail): Promise<void> => {
    await api().deleteEntry(target.id);
    forgetSelected(target.id);
    toast(`Deleted entry ${target.entry_date}`);
    setDeleting(null);
    const next = neighbors.older ?? neighbors.newer;
    if (next !== null) {
      const remaining = context.newestFirst.filter((other) => other !== id);
      openEntry.value = { id: next, context: { ...context, newestFirst: remaining }, direction: next === neighbors.older ? -1 : 1 };
    } else {
      closeEntry();
    }
    bumpData();
  };

  const statusLabel = entry === null ? '' : ENTRY_STATUS_LABELS[entry.status];
  const slide = direction === 1 ? 'slide-from-right' : direction === -1 ? 'slide-from-left' : '';

  return (
    <Dialog
      label={entry?.title ?? 'Entry'}
      onClose={close}
      class="dialog-entry"
      closeOnBackdrop
      testId="entry-modal"
      onKeyDown={onKeyDown}
      initialFocus={sheetRef}
    >
      <div class="entry-frame">
        <NavBar side="older" target={neighbors.older} loading={neighbors.loading} onGo={() => { go(-1); }} />
        <article
          class="entry-sheet"
          ref={sheetRef}
          tabIndex={-1}
          aria-labelledby="entry-modal-title"
          onPointerDown={onPointerDown}
          onPointerUp={onPointerUp}
          data-entry-id={id}
        >
          <header class="entry-head">
            <div class="entry-meta">
              {entry !== null && (
                <span class="entry-status" title={ENTRY_STATUS_HINTS[entry.status]}>
                  <StatusMarker kind={entryMarker(entry.status)} label={statusLabel} decorative />
                  <span>{statusLabel}</span>
                </span>
              )}
              {entry !== null && (
                <span class="mono entry-when" title={`Written ${formatDateTime(entry.created_at, true)}`}>
                  {entry.entry_date} {weekday(entry.entry_date)}
                  <span class="muted"> {formatTime(entry.created_at)}</span>
                </span>
              )}
              {entry !== null && (
                <span class="entry-source muted" title={entry.source === 'audio' ? 'Recorded as audio and transcribed' : 'Typed'}>
                  {entry.source === 'audio' ? 'Recorded' : 'Typed'}
                </span>
              )}
              {entry !== null && entry.revision > 1 && (
                <span class="entry-rev mono muted" title={`Revision ${entry.revision}: the text was edited and processed again`}>
                  rev {entry.revision}
                </span>
              )}
            </div>
            <div class="entry-actions">
                <button
                  type="button"
                  class={wide ? 'button button-small' : 'icon-button'}
                  disabled={entry === null}
                  title="Copy this entry as plain text"
                  aria-label="Copy entry"
                  data-testid="modal-copy"
                  onClick={() => {
                    if (entry === null) return;
                    void copyEntry(entry).then(setCopyError);
                  }}
                >
                  <Icon name="copy" size={16} />
                  {wide && <span>Copy</span>}
                </button>
                <button
                  type="button"
                  class={wide ? 'button button-small' : 'icon-button'}
                  disabled={detail === null}
                  title={detail === null ? 'Available once the entry has loaded' : 'Edit the date, title, tags or text'}
                  aria-label="Edit entry"
                  data-testid="modal-edit"
                  onClick={() => {
                    setEditing(detail);
                  }}
                >
                  <Icon name="edit" size={16} />
                  {wide && <span>Edit</span>}
                </button>
                <button
                  type="button"
                  class="icon-button"
                  disabled={detail === null}
                  title={detail === null ? 'Available once the entry has loaded, so the confirmation can say what else is deleted' : 'Delete this entry'}
                  aria-label="Delete entry"
                  data-testid="modal-delete"
                  onClick={() => {
                    setDeleting(detail);
                  }}
                >
                  <Icon name="trash" size={16} />
                </button>
                <button type="button" class="icon-button" title="Close (Esc)" aria-label="Close" data-testid="modal-close" onClick={close}>
                  <Icon name="close" size={18} />
                </button>
            </div>
            <h2 id="entry-modal-title" class={entry?.title == null ? 'entry-title is-untitled' : 'entry-title'} data-testid="modal-title">
              {entry === null ? 'Loading' : entry.title ?? 'Untitled'}
            </h2>
          </header>
          <div class="entry-scroll" ref={bodyRef}>
            <div class={`entry-content ${slide}`} key={id}>
              {error !== null && <ErrorNotice error={error} />}
              {neighbors.error !== null && <ErrorNotice error={neighbors.error} compact />}
              {copyError !== null && (
                <ErrorNotice
                  error={copyError}
                  compact
                  onDismiss={() => {
                    setCopyError(null);
                  }}
                />
              )}
              {entry !== null && (
                <EntryBody
                  entry={entry}
                  detail={detail}
                  onCopyError={setCopyError}
                  onChanged={() => {
                    bumpData();
                  }}
                />
              )}
            </div>
          </div>
        </article>
        <NavBar side="newer" target={neighbors.newer} loading={neighbors.loading} onGo={() => { go(1); }} />
      </div>
      {editing !== null && (
        <EntryEditor
          mode="edit"
          entry={editing}
          onClose={() => {
            setEditing(null);
          }}
        />
      )}
      {deleting !== null && (
        <ConfirmDialog
          title="Delete entry?"
          action="Delete entry"
          destructive
          onCancel={() => {
            setDeleting(null);
          }}
          onConfirm={() => remove(deleting)}
        >
          <p>
            <span class="mono">{deleting.entry_date}</span> {deleting.title ?? 'Untitled'}
          </p>
          <p>This removes the entry, its processing results{deleting.source === 'audio' ? ' and its recording' : ''}. It cannot be undone.</p>
          {deleting.compilation_count > 0 && (
            <p data-testid="delete-compilations">
              It also deletes <strong>{deleting.compilation_count === 1 ? '1 compilation' : `${deleting.compilation_count} compilations`}</strong> that
              cite this entry.
            </p>
          )}
        </ConfirmDialog>
      )}
    </Dialog>
  );
}

export function EntryModal(): JSX.Element | null {
  const open = openEntry.value;
  if (open === null) return null;
  return <EntryModalInner open={open} />;
}
