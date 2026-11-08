/** Pieces shared by the entry list and the search results: head line, selection bar, table header. */

import type { ComponentChildren, JSX } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { EntrySummary } from '../../api/types';
import { Icon } from '../../components/Icon';
import { plural } from '../../lib/format';
import { WIDE, useMediaQuery } from '../../hooks/useMediaQuery';
import { readPref, writePref } from '../../lib/prefs';
import { clearSelection, selectAll, selectedCount, selection } from '../../state/selection';
import { density, setDensity, updateView, view } from '../../state/view';

export type DialogRequest = 'export' | 'compile';

export interface ListHeadProps {
  readonly children: ComponentChildren;
  readonly visible: readonly EntrySummary[];
  readonly onAction: (action: DialogRequest) => void;
  readonly busy?: boolean;
  /** Offer the Lines/Summaries switch; results always show their matching text. */
  readonly density?: boolean;
  /** Controls kept visible after the text, which may be cut short on narrow screens. */
  readonly actions?: ComponentChildren;
}

/** One fixed-height line above the rows; while rows are selected it carries the selection actions. */
export function ListHead({ children, visible, onAction, busy = false, density: showDensity = true, actions }: ListHeadProps): JSX.Element {
  const wide = useMediaQuery(WIDE);
  const count = selectedCount.value;
  const allVisibleSelected = visible.length > 0 && visible.every((entry) => selection.value.has(entry.id));
  if (count > 0) {
    return (
      <div class="list-head is-selecting" data-testid="selection-bar">
        <span class="list-head-text">
          <strong class="mono">{count}</strong> selected
        </span>
        {!allVisibleSelected && visible.length > 0 && (
          <button
            type="button"
            class="link-button"
            title={`Select all ${visible.length} shown`}
            onClick={() => {
              selectAll(visible);
            }}
          >
            {wide ? `Select all ${visible.length} shown` : `All ${visible.length}`}
          </button>
        )}
        <span class="list-head-spacer" />
        <button
          type="button"
          class="button button-small"
          title="Copy or download the selected entries as plain text, Markdown or JSON"
          data-testid="open-export"
          onClick={() => {
            onAction('export');
          }}
        >
          <Icon name="export" size={14} />
          <span>Export</span>
        </button>
        <button
          type="button"
          class="button button-small"
          title="Compile resume bullets, STAR stories and open threads from the selected entries"
          data-testid="open-compile"
          onClick={() => {
            onAction('compile');
          }}
        >
          <Icon name="compile" size={14} />
          <span>Compile</span>
        </button>
        {wide ? (
          <button type="button" class="button button-small button-quiet" title="Clear the selection" onClick={clearSelection} data-testid="clear-selection">
            Clear
          </button>
        ) : (
          <button type="button" class="icon-button icon-button-small" title="Clear the selection" aria-label="Clear the selection" onClick={clearSelection} data-testid="clear-selection">
            <Icon name="close" size={14} />
          </button>
        )}
      </div>
    );
  }
  return (
    <div class="list-head">
      <span class="list-head-text" data-testid="list-head-text">
        {children}
      </span>
      {actions}
      {busy && <span class="busy-dot" title="Loading" aria-label="Loading" role="img" />}
      <span class="list-head-spacer" />
      {showDensity && <DensityToggle />}
    </div>
  );
}

function DensityToggle(): JSX.Element {
  const current = density.value;
  return (
    <div class="segmented segmented-small" role="group" aria-label="Row density">
      <button
        type="button"
        class={current === 'lines' ? 'segment is-on' : 'segment'}
        aria-pressed={current === 'lines'}
        title="One line per entry"
        onClick={() => {
          setDensity('lines');
        }}
      >
        Lines
      </button>
      <button
        type="button"
        class={current === 'summaries' ? 'segment is-on' : 'segment'}
        aria-pressed={current === 'summaries'}
        title="Show each entry's summary under its title"
        data-testid="density-summaries"
        onClick={() => {
          setDensity('summaries');
        }}
      >
        Summaries
      </button>
    </div>
  );
}

const LABELS_KEY = 'labels-width';
const LABELS_DEFAULT = 240;
const LABELS_MIN = 120;
const LABELS_MAX = 520;

function clampWidth(value: number): number {
  return Math.round(Math.min(LABELS_MAX, Math.max(LABELS_MIN, value)));
}

function storedWidth(): number {
  const raw = Number(readPref(LABELS_KEY));
  return Number.isFinite(raw) && raw > 0 ? clampWidth(raw) : LABELS_DEFAULT;
}

/** Width of the labels column, remembered per viewer and applied as a custom property. */
export function useLabelsWidth(scroller: { readonly current: HTMLElement | null }): {
  readonly width: number;
  readonly setWidth: (value: number) => void;
  readonly reset: () => void;
} {
  const [width, setState] = useState(storedWidth);
  useEffect(() => {
    scroller.current?.style.setProperty('--labels-w', `${width}px`);
  }, [scroller, width]);
  const setWidth = (value: number): void => {
    const next = clampWidth(value);
    setState(next);
    void writePref(LABELS_KEY, next === LABELS_DEFAULT ? null : String(next));
  };
  return { width, setWidth, reset: () => { setWidth(LABELS_DEFAULT); } };
}

export interface TableHeaderProps {
  readonly visible: readonly EntrySummary[];
  readonly labelsWidth: number;
  readonly onLabelsWidth: (value: number) => void;
  readonly onResetWidth: () => void;
  /** Present for the chronological list, absent for ranked results. */
  readonly sortable: boolean;
  readonly figureLabel?: string;
}

export function TableHeader({ visible, labelsWidth, onLabelsWidth, onResetWidth, sortable, figureLabel }: TableHeaderProps): JSX.Element {
  const order = view.value.order;
  const allSelected = visible.length > 0 && visible.every((entry) => selection.value.has(entry.id));
  const drag = useRef<{ readonly x: number; readonly width: number } | null>(null);

  const onPointerDown = (event: PointerEvent): void => {
    event.preventDefault();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    drag.current = { x: event.clientX, width: labelsWidth };
  };
  const onPointerMove = (event: PointerEvent): void => {
    if (drag.current === null) return;
    // Dragging the divider left widens the labels column.
    onLabelsWidth(drag.current.width - (event.clientX - drag.current.x));
  };
  const onPointerUp = (): void => {
    drag.current = null;
  };

  return (
    <div class="list-header" role="row" data-testid="list-header">
      <span class="cell cell-select" role="columnheader">
        <button
          type="button"
          class={allSelected ? 'select-box is-on' : 'select-box'}
          aria-pressed={allSelected}
          aria-label={allSelected ? 'Deselect all shown' : 'Select all shown'}
          title={allSelected ? 'Deselect all shown entries' : 'Select all shown entries'}
          onClick={() => {
            if (allSelected) {
              const next = new Map(selection.value);
              for (const entry of visible) next.delete(entry.id);
              selection.value = next;
            } else {
              selectAll(visible);
            }
          }}
        >
          <svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">
            <rect class="select-frame" x="1.5" y="1.5" width="11" height="11" rx="1.5" />
            {allSelected && <path class="select-check" d="m4 7.25 2 2 4-4.5" />}
          </svg>
        </button>
      </span>
      <span class="cell cell-status" role="columnheader" title="Processing status: ring queued, half ring processing, disc done, triangle needs attention, cross failed">
        <span class="visually-hidden">Status</span>
      </span>
      <span class="cell cell-date" role="columnheader">
        {sortable ? (
          <button
            type="button"
            class="head-sort"
            title={order === 'desc' ? 'Newest first. Click for oldest first.' : 'Oldest first. Click for newest first.'}
            data-testid="sort-date"
            onClick={() => {
              updateView({ order: order === 'desc' ? 'asc' : 'desc' });
            }}
          >
            <span>Date</span>
            <Icon name={order === 'desc' ? 'sort-desc' : 'sort-asc'} size={14} />
          </button>
        ) : (
          <span class="head-label">Date</span>
        )}
      </span>
      <span class="cell cell-main head-label" role="columnheader">
        Entry
      </span>
      <span class="cell cell-labels head-label" role="columnheader" title="Projects, then your tags; hover a row for skills">
        <span
          class="col-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the labels column"
          aria-valuenow={labelsWidth}
          aria-valuemin={LABELS_MIN}
          aria-valuemax={LABELS_MAX}
          tabIndex={0}
          title="Drag to resize; arrow keys work too"
          data-testid="labels-resizer"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
              event.preventDefault();
              onLabelsWidth(labelsWidth + (event.key === 'ArrowLeft' ? 16 : -16));
            }
          }}
        />
        <span>Labels</span>
        {labelsWidth !== LABELS_DEFAULT && (
          <button type="button" class="icon-button icon-button-small width-reset" title="Reset the column width" aria-label="Reset the column width" onClick={onResetWidth}>
            <Icon name="width-reset" size={14} />
          </button>
        )}
      </span>
      <span class="cell cell-figure head-label" role="columnheader">
        {figureLabel}
      </span>
      <span class="cell cell-source" role="columnheader" title="Source: a waveform marks audio entries">
        <span class="visually-hidden">Source</span>
      </span>
      <span class="cell cell-copy" role="columnheader">
        <span class="visually-hidden">Copy</span>
      </span>
    </div>
  );
}

export function EmptyState({ children, action }: { readonly children: ComponentChildren; readonly action?: ComponentChildren }): JSX.Element {
  return (
    <div class="empty-state" data-testid="empty-state">
      <p class="empty-text">{children}</p>
      {action !== undefined && <div class="empty-action">{action}</div>}
    </div>
  );
}

export function describeCount(shown: number, total: number, noun = 'entry', nounPlural = 'entries'): string {
  return shown >= total ? plural(total, noun, nounPlural) : `${shown} of ${plural(total, noun, nounPlural)}`;
}

export function clearFacetFilters(): void {
  updateView({ tags: [], projects: [], skills: [] });
}
