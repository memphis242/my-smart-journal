/**
 * Hollow state markers. Shape and color both encode the state, so the marker
 * still reads without color: ring = not started, half-filled ring = in
 * progress, filled disc = complete, triangle = needs attention, cross = failed,
 * dashed ring = waiting on a failure.
 */

import type { JSX } from 'preact';
import type { CompilationStatus, EntryStatus, JobStatus, QueueItemStatus } from '../api/types';
import { unreachable } from '../lib/assert';

export type MarkerKind = 'queued' | 'progress' | 'done' | 'attention' | 'failed' | 'blocked' | 'muted';

export function entryMarker(status: EntryStatus): MarkerKind {
  switch (status) {
    case 'queued':
      return 'queued';
    case 'processing':
      return 'progress';
    case 'done':
      return 'done';
    case 'needs_attention':
      return 'attention';
    case 'failed':
      return 'failed';
    default:
      return unreachable(status, 'entry status');
  }
}

export function jobMarker(status: JobStatus | QueueItemStatus, blocked: boolean): MarkerKind {
  if (blocked && status === 'queued') return 'blocked';
  switch (status) {
    case 'queued':
      return 'queued';
    case 'running':
      return 'progress';
    case 'done':
      return 'done';
    case 'failed':
      return 'failed';
    case 'superseded':
      return 'muted';
    default:
      return unreachable(status, 'job status');
  }
}

export function compilationMarker(status: CompilationStatus): MarkerKind {
  switch (status) {
    case 'queued':
      return 'queued';
    case 'running':
      return 'progress';
    case 'done':
      return 'done';
    case 'failed':
      return 'failed';
    default:
      return unreachable(status, 'compilation status');
  }
}

function shape(kind: MarkerKind): JSX.Element {
  switch (kind) {
    case 'queued':
    case 'muted':
      return <circle class="mk-line" cx="6" cy="6" r="4.25" />;
    case 'blocked':
      return <circle class="mk-line mk-dashed" cx="6" cy="6" r="4.25" />;
    case 'progress':
      return (
        <>
          <circle class="mk-line" cx="6" cy="6" r="4.25" />
          <path class="mk-fill" d="M1.75 6a4.25 4.25 0 0 0 8.5 0z" />
        </>
      );
    case 'done':
      return <circle class="mk-line mk-fill" cx="6" cy="6" r="4.25" />;
    case 'attention':
      return <path class="mk-line" d="M6 1.4 10.7 10H1.3z" />;
    case 'failed':
      return <path class="mk-line mk-cross" d="m2.4 2.4 7.2 7.2m0-7.2-7.2 7.2" />;
    default:
      return unreachable(kind, 'marker kind');
  }
}

export interface StatusMarkerProps {
  readonly kind: MarkerKind;
  /** Tooltip and accessible name, for example "Needs attention". */
  readonly label: string;
  readonly size?: 10 | 12 | 14;
}

export function StatusMarker({ kind, label, size = 12 }: StatusMarkerProps): JSX.Element {
  return (
    <span class={`marker marker-${kind}`} title={label} role="img" aria-label={label} data-marker={kind}>
      <svg viewBox="0 0 12 12" width={size} height={size} aria-hidden="true" focusable="false">
        {shape(kind)}
      </svg>
    </span>
  );
}
