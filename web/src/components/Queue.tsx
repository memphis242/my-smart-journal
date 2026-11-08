/**
 * The processing queue: a compact indicator in the top bar, and a side sheet
 * with the batch and worker state, every waiting or failed job, and retry.
 */

import { signal } from '@preact/signals';
import type { JSX } from 'preact';
import { useState } from 'preact/hooks';
import { toAppError, type AppError } from '../api/errors';
import type { QueueItem, QueueSnapshot } from '../api/types';
import { formatDistance, formatTime, formatWhen } from '../lib/dates';
import { JOB_STATUS_LABELS, plural, stageLabel } from '../lib/format';
import { WIDE, useMediaQuery } from '../hooks/useMediaQuery';
import { useNow } from '../hooks/useNow';
import { api } from '../state/app';
import { showEntry } from '../state/entry-modal';
import { batchRequest, clearBatchError, isBusy, lastBatchError, pokeQueue, queueState, runBatchNow, workerStale } from '../state/queue';
import { toast } from '../state/toasts';
import { Dialog, DialogHeader } from './Dialog';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { StatusMarker, jobMarker, type MarkerKind } from './StatusMarker';

export const queueSheetOpen = signal(false);

function openSheet(): void {
  queueSheetOpen.value = true;
  pokeQueue();
}

interface Summary {
  readonly kind: MarkerKind;
  readonly text: string;
  readonly short: string;
  readonly failed: number;
  readonly stale: boolean;
  readonly batchFailed: boolean;
}

function summarize(snapshot: QueueSnapshot, now: number): Summary {
  const { counts } = snapshot;
  const waiting = counts.queued + counts.running + counts.blocked;
  const stale = workerStale(snapshot, now);
  const failed = counts.failed;
  let text: string;
  if (snapshot.batch.running) {
    text = waiting > 0 ? `Batch running, ${waiting} left` : 'Batch running';
  } else if (waiting > 0) {
    const next = snapshot.worker.next_run_at;
    text = !stale && next !== null ? `${waiting} queued, next ${formatTime(next)}` : `${waiting} queued`;
  } else {
    text = 'Queue empty';
  }
  const batchFailed = snapshot.batch.last_error !== null && !snapshot.batch.running;
  const kind: MarkerKind = isBusy(snapshot)
    ? 'progress'
    : failed > 0 || batchFailed
      ? 'failed'
      : stale
        ? 'attention'
        : waiting > 0
          ? 'queued'
          : 'done';
  // Narrow screens show one figure: failures first, since they need action.
  const short = failed > 0 ? `${failed} failed` : batchFailed ? 'Batch failed' : String(waiting);
  return { kind, text, short, failed, stale, batchFailed };
}

export function QueueIndicator(): JSX.Element {
  const wide = useMediaQuery(WIDE);
  const now = useNow();
  const { snapshot, error } = queueState.value;
  const request = batchRequest.value;

  if (snapshot === null) {
    const label = error === null ? 'Loading the queue' : `Queue unavailable (${error.code})`;
    return (
      <button type="button" class="queue-indicator" title={label} data-testid="queue-indicator" onClick={openSheet}>
        <StatusMarker kind={error === null ? 'muted' : 'failed'} label={label} />
        {wide && <span class="queue-text">{error === null ? 'Queue' : 'Queue unavailable'}</span>}
      </button>
    );
  }

  const summary = summarize(snapshot, now);
  const title = [
    summary.text,
    summary.failed > 0 ? `${summary.failed} failed` : null,
    summary.stale ? 'worker not running' : null,
    summary.batchFailed ? `last batch failed (${snapshot.batch.last_error?.code ?? ''})` : null,
    'Open the queue',
  ]
    .filter((part) => part !== null)
    .join('; ');
  const canRun = !snapshot.batch.running && !request.pending;

  return (
    <div class="queue-bar" data-testid="queue-bar">
      <button type="button" class="queue-indicator" title={title} data-testid="queue-indicator" onClick={openSheet}>
        <StatusMarker kind={summary.kind} label={summary.text} />
        <span class={!wide && (summary.failed > 0 || summary.batchFailed) ? 'queue-text queue-failed' : 'queue-text'}>
          {wide ? summary.text : summary.short}
          {wide && summary.failed > 0 && <span class="queue-failed">{summary.failed} failed</span>}
          {wide && summary.stale && <span class="queue-stale">Worker not running</span>}
          {wide && summary.batchFailed && <span class="queue-failed">Last batch failed</span>}
        </span>
      </button>
      {wide && (
        <button
          type="button"
          class="button button-small"
          disabled={!canRun}
          title={snapshot.batch.running ? 'A batch is running' : 'Process every queued entry now instead of waiting for the worker'}
          data-testid="run-batch"
          onClick={() => {
            void runBatchNow().then(() => {
              if (batchRequest.value.error !== null) openSheet();
            });
          }}
        >
          {snapshot.batch.running ? 'Running' : request.pending ? 'Starting' : 'Run now'}
        </button>
      )}
    </div>
  );
}

function JobRow({ item, now }: { readonly item: QueueItem; readonly now: number }): JSX.Element {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const blocked = item.blocked_by.length > 0;
  const statusLabel = blocked && item.status === 'queued' ? JOB_STATUS_LABELS.blocked : JOB_STATUS_LABELS[item.status];
  const label = stageLabel(item.kind, item.target, item.turn);
  const waitUntil = item.not_before !== null && Date.parse(item.not_before) > now ? item.not_before : null;

  const retry = async (): Promise<void> => {
    setPending(true);
    setError(null);
    try {
      await api().retryJob(item.job_id);
      toast(`${label} queued again`);
      pokeQueue();
    } catch (caught) {
      setError(toAppError(caught, 'Retry job'));
    } finally {
      setPending(false);
    }
  };

  return (
    <li class={`job-row job-${item.status}`} data-testid="queue-job" data-status={item.status}>
      <div class="job-line">
        <StatusMarker kind={jobMarker(item.status, blocked)} label={statusLabel} />
        <span class="job-stage">{label}</span>
        <span class="job-status" title={blocked ? `Waiting on failed ${item.blocked_by.map((b) => stageLabel(b.kind, null, null).toLowerCase()).join(', ')}` : undefined}>
          {statusLabel}
        </span>
        {waitUntil !== null && (
          <span class="job-wait mono" title="Automatic retry is scheduled; a forced batch also waits for it">
            retry {formatTime(waitUntil, true)}
          </span>
        )}
        {item.status === 'failed' && (
          <button
            type="button"
            class="button button-small job-retry"
            disabled={pending}
            title="Reset this job's attempts and queue it for the next batch"
            data-testid="retry-job"
            onClick={() => void retry()}
          >
            <Icon name="retry" size={14} />
            <span>{pending ? 'Retrying' : 'Retry'}</span>
          </button>
        )}
      </div>
      {item.last_error !== null && (
        <details class="job-error" open={item.status === 'failed'}>
          <summary>{item.status === 'failed' ? 'Error' : 'Last error'}</summary>
          <p class="job-error-text">{item.last_error}</p>
        </details>
      )}
      {error !== null && <ErrorNotice error={error} compact onDismiss={() => { setError(null); }} />}
    </li>
  );
}

interface Group {
  readonly key: string;
  readonly entryId: string | null;
  readonly date: string | null;
  readonly title: string | null;
  readonly items: QueueItem[];
}

function groupItems(items: readonly QueueItem[]): readonly Group[] {
  const groups = new Map<string, Group>();
  for (const item of items) {
    const key = item.entry_id ?? '__compile__';
    let group = groups.get(key);
    if (group === undefined) {
      group = { key, entryId: item.entry_id, date: item.entry_date, title: item.entry_title, items: [] };
      groups.set(key, group);
    }
    group.items.push(item);
  }
  return [...groups.values()];
}

function openFromQueue(entryId: string): void {
  showEntry(entryId, { newestFirst: [entryId], previews: new Map(), filters: {}, complete: false });
}

function StatusBlock({ snapshot, now }: { readonly snapshot: QueueSnapshot; readonly now: number }): JSX.Element {
  const { batch, worker } = snapshot;
  const stale = workerStale(snapshot, now);
  const intervalMin = Math.round(worker.interval_s / 60);
  return (
    <div class="queue-status">
      <p class="queue-status-line" data-testid="batch-state">
        <StatusMarker kind={batch.running ? 'progress' : 'muted'} label={batch.running ? 'Batch running' : 'Idle'} />
        {batch.running ? (
          <span>
            Batch running since <span class="mono">{batch.started_at === null ? 'now' : formatTime(batch.started_at, true)}</span>
            {batch.trigger !== null && <span class="muted"> ({batch.trigger === 'manual' ? 'started here' : 'scheduled'})</span>}
          </span>
        ) : batch.last_finished_at !== null ? (
          <span>
            Idle. Last batch finished <span class="mono">{formatWhen(batch.last_finished_at)}</span>
            {batch.last_result !== null && (
              <span class="muted">
                : {batch.last_result.processed} processed, {batch.last_result.failed} failed
              </span>
            )}
          </span>
        ) : (
          <span>Idle. No batch has run yet.</span>
        )}
      </p>
      {stale ? (
        <div class="notice notice-attention" data-testid="worker-stale">
          <StatusMarker kind="attention" label="Worker not running" />
          <div class="notice-main">
            <p class="notice-message">
              <strong>Worker not running.</strong>{' '}
              {worker.last_seen_at === null ? (
                'No heartbeat has been seen.'
              ) : (
                <>
                  Last heartbeat <span class="mono">{formatWhen(worker.last_seen_at)}</span> ({formatDistance(worker.last_seen_at, new Date(now))}); it should
                  report every {intervalMin} min.
                </>
              )}
            </p>
            <p class="notice-hint">
              Start it on the server with <code>msj worker</code>. Until then, entries wait unless you run the batch here.
            </p>
          </div>
        </div>
      ) : (
        <p class="queue-status-line">
          <StatusMarker kind="done" label="Worker running" />
          <span>
            Worker seen <span class="mono">{worker.last_seen_at === null ? '-' : formatTime(worker.last_seen_at, true)}</span>, every {intervalMin} min.
            {worker.next_run_at !== null && (
              <>
                {' '}
                Next run <span class="mono">{formatTime(worker.next_run_at)}</span>{' '}
                <span class="muted">({formatDistance(worker.next_run_at, new Date(now))})</span>
              </>
            )}
          </span>
        </p>
      )}
    </div>
  );
}

export function QueueSheet(): JSX.Element | null {
  const now = useNow(5000);
  if (!queueSheetOpen.value) return null;
  const { snapshot, error } = queueState.value;
  const request = batchRequest.value;
  const close = (): void => {
    queueSheetOpen.value = false;
    clearBatchError();
  };
  const groups = snapshot === null ? [] : groupItems(snapshot.items);

  return (
    <Dialog label="Processing queue" onClose={close} class="dialog-sheet" closeOnBackdrop testId="queue-sheet">
      <div class="dialog-panel sheet-panel">
        <DialogHeader title="Processing queue" onClose={close}>
          {snapshot !== null && (
            <button
              type="button"
              class="button button-primary button-small"
              disabled={snapshot.batch.running || request.pending}
              title="Process every queued entry now instead of waiting for the worker"
              data-testid="sheet-run-batch"
              onClick={() => void runBatchNow()}
            >
              {snapshot.batch.running ? 'Batch running' : request.pending ? 'Starting' : 'Run batch now'}
            </button>
          )}
        </DialogHeader>
        <div class="dialog-body sheet-body">
          {error !== null && <ErrorNotice error={error} onRetry={pokeQueue} />}
          {request.error !== null && <ErrorNotice error={request.error} onDismiss={clearBatchError} />}
          {snapshot !== null && (
            <>
              <StatusBlock snapshot={snapshot} now={now} />
              {(() => {
                const failure = snapshot.batch.running ? null : lastBatchError(snapshot);
                return failure === null ? null : <ErrorNotice error={failure} />;
              })()}
              <p class="queue-counts" data-testid="queue-counts">
                <span title="Waiting for a batch">
                  <span class="mono">{snapshot.counts.queued}</span> queued
                </span>
                <span title="Being processed now">
                  <span class="mono">{snapshot.counts.running}</span> running
                </span>
                <span class={snapshot.counts.failed > 0 ? 'count-failed' : undefined} title="Failed; retry after fixing the cause">
                  <span class="mono">{snapshot.counts.failed}</span> failed
                </span>
                <span title="Queued, but waiting on a failed step">
                  <span class="mono">{snapshot.counts.blocked}</span> blocked
                </span>
              </p>
              {groups.length === 0 ? (
                <p class="empty-note">Nothing is waiting. New entries wait here for the next batch.</p>
              ) : (
                <ul class="queue-groups">
                  {groups.map((group) => (
                    <li class="queue-group" key={group.key}>
                      <div class="queue-group-head">
                        {group.entryId === null ? (
                          <span class="queue-group-title">Compilations</span>
                        ) : (
                          <button
                            type="button"
                            class="link-button queue-group-title"
                            title="Open this entry"
                            onClick={() => {
                              if (group.entryId !== null) openFromQueue(group.entryId);
                            }}
                          >
                            {group.date !== null && <span class="mono date">{group.date}</span>}
                            <span class="queue-group-name">{group.title ?? 'Untitled entry'}</span>
                          </button>
                        )}
                        <span class="muted queue-group-count">
                          {plural(group.items.length, 'step')}
                          {group.items.some((item) => item.blocked_by.length > 0) &&
                            `, ${group.items.filter((item) => item.blocked_by.length > 0).length} waiting on a failure`}
                        </span>
                      </div>
                      <ul class="job-list">
                        {group.items.map((item) => (
                          <JobRow item={item} now={now} key={item.job_id} />
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              )}
              {snapshot.items.length >= 200 && <p class="muted">Showing the oldest 200 jobs.</p>}
            </>
          )}
        </div>
      </div>
    </Dialog>
  );
}
