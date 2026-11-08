/**
 * Ruled entry rows shared by the list and the search results.
 *
 * Plain click (or Enter) opens the entry. Selection follows the desktop
 * convention: Ctrl/Cmd-click toggles a row, Shift-click selects a range; the
 * hollow box at the row start toggles too, for touch and for discoverability.
 */

import type { ComponentChildren, JSX } from 'preact';
import { useRef } from 'preact/hooks';
import type { EntrySummary } from '../../api/types';
import { Icon } from '../../components/Icon';
import { StatusMarker, entryMarker } from '../../components/StatusMarker';
import { copyText } from '../../lib/clipboard';
import { weekday } from '../../lib/dates';
import { ENTRY_STATUS_HINTS, ENTRY_STATUS_LABELS, entryPlainText, headline } from '../../lib/format';
import { selectRange, selection, toggleSelected } from '../../state/selection';
import { toast } from '../../state/toasts';
import { density } from '../../state/view';

export interface RowsContext {
  /** Rows in display order, for Shift-click ranges and arrow-key movement. */
  readonly visible: readonly EntrySummary[];
  readonly onOpen: (entry: EntrySummary) => void;
}

export async function copyEntry(entry: EntrySummary): Promise<void> {
  try {
    await copyText(entryPlainText(entry));
    toast(`Copied ${entry.entry_date}${entry.title === null ? '' : `  ${entry.title}`}`);
  } catch (error) {
    toast(error instanceof Error ? error.message : 'Copy failed');
  }
}

function Labels({ entry }: { readonly entry: EntrySummary }): JSX.Element | null {
  const projects = entry.projects;
  const tags = entry.tags;
  if (projects.length === 0 && tags.length === 0) return null;
  const shownProjects = projects.slice(0, 2);
  const hiddenProjects = projects.length - shownProjects.length;
  const allText = [
    projects.length > 0 ? `Projects: ${projects.join(', ')}` : null,
    tags.length > 0 ? `Tags: ${tags.join(', ')}` : null,
    entry.skills.length > 0 ? `Skills: ${entry.skills.join(', ')}` : null,
  ]
    .filter((part) => part !== null)
    .join('\n');
  return (
    <span class="row-labels" title={allText}>
      {shownProjects.map((project) => (
        <span class="chip chip-project" key={project}>
          {project}
        </span>
      ))}
      {hiddenProjects > 0 && <span class="chip chip-more">+{hiddenProjects}</span>}
      {tags.length > 0 && <span class="row-tags">{tags.join(' ')}</span>}
    </span>
  );
}

function focusRow(container: HTMLElement | null, from: HTMLElement, step: 1 | -1): void {
  if (container === null) return;
  const rows = [...container.querySelectorAll<HTMLElement>('[data-row-id]')];
  const index = rows.indexOf(from);
  const target = rows[index + step];
  if (target === undefined) return;
  target.focus({ preventScroll: true });
  // Keep the row visible by moving this list's own scroller only, never the window.
  const scroller = target.closest<HTMLElement>('.list-scroll');
  if (scroller === null) return;
  const head = scroller.querySelector<HTMLElement>('.list-header');
  const top = target.offsetTop - (head?.offsetHeight ?? 0);
  const bottom = target.offsetTop + target.offsetHeight;
  if (top < scroller.scrollTop) scroller.scrollTop = top;
  else if (bottom > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = bottom - scroller.clientHeight;
}

export interface EntryRowProps {
  readonly entry: EntrySummary;
  readonly context: RowsContext;
  /** Hide the date when the previous row has the same day. */
  readonly continuesDay?: boolean;
  /** Extra line under the headline: a snippet or passage. */
  readonly hit?: ComponentChildren;
  /** Right-aligned figure such as a relevance score. */
  readonly figure?: string | null;
  readonly figureTitle?: string;
}

export function EntryRow({ entry, context, continuesDay = false, hit, figure = null, figureTitle }: EntryRowProps): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const selected = selection.value.has(entry.id);
  const statusLabel = ENTRY_STATUS_LABELS[entry.status];
  const summaryLine = entry.title !== null && entry.title.trim() !== '' ? (entry.summary ?? null) : null;
  const pendingAudio = entry.source === 'audio' && entry.text.trim() === '' && entry.summary === null;
  const main = pendingAudio && entry.title === null ? 'Voice recording, transcript pending' : headline(entry);

  const onClick = (event: MouseEvent): void => {
    if ((event.target as HTMLElement).closest('button') !== null) return;
    if (event.shiftKey) {
      event.preventDefault();
      selectRange(context.visible, entry);
    } else if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      toggleSelected(entry);
    } else {
      context.onOpen(entry);
    }
  };

  const onKeyDown = (event: KeyboardEvent): void => {
    if (event.target !== ref.current) return;
    switch (event.key) {
      case 'Enter':
        event.preventDefault();
        context.onOpen(entry);
        break;
      case ' ':
        event.preventDefault();
        if (event.shiftKey) selectRange(context.visible, entry);
        else toggleSelected(entry);
        break;
      case 'ArrowDown':
      case 'ArrowUp':
        event.preventDefault();
        if (ref.current !== null) {
          focusRow(ref.current.closest('.list-scroll'), ref.current, event.key === 'ArrowDown' ? 1 : -1);
        }
        break;
      default:
        break;
    }
  };

  const showSummary = density.value === 'summaries' || hit !== undefined;

  return (
    <div
      ref={ref}
      class={`entry-row${selected ? ' is-selected' : ''}${showSummary ? ' is-tall' : ''}`}
      role="row"
      tabIndex={0}
      aria-selected={selected}
      data-row-id={entry.id}
      data-testid="entry-row"
      data-status={entry.status}
      onClick={onClick}
      onKeyDown={onKeyDown}
    >
      <span class="cell cell-select" role="cell">
        <button
          type="button"
          class={selected ? 'select-box is-on' : 'select-box'}
          aria-pressed={selected}
          aria-label={selected ? 'Deselect entry' : 'Select entry'}
          title={selected ? 'Deselect (Space)' : 'Select for export or compile (Space; Shift for a range)'}
          data-testid="row-select"
          onClick={(event) => {
            event.stopPropagation();
            if (event.shiftKey) selectRange(context.visible, entry);
            else toggleSelected(entry);
          }}
        >
          <svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">
            <rect class="select-frame" x="1.5" y="1.5" width="11" height="11" rx="1.5" />
            {selected && <path class="select-check" d="m4 7.25 2 2 4-4.5" />}
          </svg>
        </button>
      </span>
      <span class="cell cell-status" role="cell">
        <StatusMarker kind={entryMarker(entry.status)} label={`${statusLabel}: ${ENTRY_STATUS_HINTS[entry.status]}`} />
      </span>
      <span class={continuesDay ? 'cell cell-date is-continued' : 'cell cell-date'} role="cell">
        <span class="date mono">{entry.entry_date}</span>
        <span class="weekday mono">{weekday(entry.entry_date)}</span>
      </span>
      <span class="cell cell-main" role="cell">
        <span class={entry.title === null ? 'row-headline is-untitled' : 'row-headline'}>{main}</span>
        {showSummary && summaryLine !== null && hit === undefined && <span class="row-summary">{summaryLine}</span>}
        {showSummary && summaryLine === null && hit === undefined && entry.title !== null && !pendingAudio && (
          <span class="row-summary">{entry.text.slice(0, 280)}</span>
        )}
        {hit !== undefined && <span class="row-hit">{hit}</span>}
      </span>
      <span class="cell cell-labels" role="cell">
        <Labels entry={entry} />
      </span>
      <span class="cell cell-figure mono" role="cell" title={figureTitle}>
        {figure}
      </span>
      <span class="cell cell-source" role="cell">
        {entry.source === 'audio' && (
          <span class="source-audio" title="Recorded as audio" aria-label="Recorded as audio" role="img">
            <Icon name="audio" size={14} />
          </span>
        )}
      </span>
      <span class="cell cell-copy" role="cell">
        <button
          type="button"
          class="icon-button icon-button-small row-copy"
          title="Copy this entry as plain text"
          aria-label="Copy entry"
          data-testid="row-copy"
          onClick={(event) => {
            event.stopPropagation();
            void copyEntry(entry);
          }}
        >
          <Icon name="copy" size={16} />
        </button>
      </span>
    </div>
  );
}
