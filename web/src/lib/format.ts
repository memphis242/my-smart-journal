/** Text formatting shared by the views: labels, counts, sizes and plain-text copies. */

import type { CompilationStatus, EntryStatus, EntrySummary, JobStatus, QueueItemStatus } from '../api/types';

export const ENTRY_STATUS_LABELS: Readonly<Record<EntryStatus, string>> = {
  queued: 'Queued',
  processing: 'Processing',
  done: 'Done',
  needs_attention: 'Needs attention',
  failed: 'Failed',
};

export const ENTRY_STATUS_HINTS: Readonly<Record<EntryStatus, string>> = {
  queued: 'Waiting for the next batch',
  processing: 'A batch is working on it now',
  done: 'Cleaned up, summarized and extracted',
  needs_attention: 'A review found claims the source does not support',
  failed: 'A processing step failed; open it to see why and retry',
};

export const JOB_STATUS_LABELS: Readonly<Record<JobStatus | QueueItemStatus | 'blocked', string>> = {
  queued: 'Queued',
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
  superseded: 'Superseded',
  blocked: 'Blocked',
};

export const COMPILATION_STATUS_LABELS: Readonly<Record<CompilationStatus, string>> = {
  queued: 'Queued',
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
};

const STAGE_NAMES: Readonly<Record<string, string>> = {
  transcribe: 'Transcribe',
  cleanup: 'Clean up',
  summarize: 'Summarize',
  extract: 'Extract',
  embed: 'Embed for search',
  compile: 'Compile',
  review: 'Review',
  revise: 'Revise',
};

const TARGET_NAMES: Readonly<Record<string, string>> = {
  cleanup: 'cleanup',
  summarize: 'summary',
  extract: 'extraction',
};

/** Human name of a pipeline job, for example "Review summary, turn 2". */
export function stageLabel(kind: string, target: string | null, turn: number | null): string {
  const base = STAGE_NAMES[kind] ?? kind.charAt(0).toUpperCase() + kind.slice(1);
  if (kind !== 'review' && kind !== 'revise') return base;
  const what = target === null ? '' : ` ${TARGET_NAMES[target] ?? target}`;
  const round = turn === null || turn === 0 ? '' : `, turn ${turn}`;
  return `${base}${what}${round}`;
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

const INTEGER = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

export function formatCount(value: number): string {
  return INTEGER.format(value);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

/** `MM:SS` (or `H:MM:SS`) for a duration in milliseconds. */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function formatScore(score: number): string {
  return score.toFixed(2);
}

/** First line of the text, for rows without a title or summary. */
export function firstLine(text: string): string {
  const trimmed = text.trim();
  const end = trimmed.search(/\n/);
  return end < 0 ? trimmed : trimmed.slice(0, end).trim();
}

/** The one-line headline a row shows: the title, else the summary, else the text. */
export function headline(entry: Pick<EntrySummary, 'title' | 'summary' | 'text'>): string {
  if (entry.title !== null && entry.title.trim() !== '') return entry.title.trim();
  if (entry.summary !== null && entry.summary.trim() !== '') return entry.summary.trim();
  return firstLine(entry.text);
}

/**
 * One entry as plain text for the clipboard, laid out like the server's
 * `text` export so pasted copies and exports look alike in Word.
 */
export function entryPlainText(entry: EntrySummary): string {
  const lines: string[] = [];
  const title = entry.title?.trim() ?? '';
  lines.push(title === '' ? entry.entry_date : `${entry.entry_date}  ${title}`);
  if (entry.summary !== null && entry.summary.trim() !== '') {
    lines.push('', 'Summary:', entry.summary.trim());
  }
  const text = entry.text.trim();
  if (text !== '') lines.push('', text);
  if (entry.tags.length > 0) lines.push('', 'Tags:', entry.tags.join(', '));
  return lines.join('\n');
}

/** Paragraphs of entry text, split on blank lines. */
export function paragraphs(text: string): readonly string[] {
  return text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter((p) => p !== '');
}
