/** Chronological entry list for the current range and filters, loaded page by page. */

import type { JSX } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { isAbort, toAppError, type AppError } from '../../api/errors';
import type { EntrySummary } from '../../api/types';
import { ErrorNotice } from '../../components/ErrorNotice';
import { describeBounds } from '../../lib/dates';
import { plural } from '../../lib/format';
import { api, dataVersion } from '../../state/app';
import { contextFor, showEntry } from '../../state/entry-modal';
import { refreshSelected } from '../../state/selection';
import { bounds, facetFilterCount, filters, savedScroll, updateView, view } from '../../state/view';
import { EntryRow, useRovingRow, type RowsContext } from './EntryRows';
import { ListNotice } from './SearchResults';
import { EmptyState, ListHead, TableHeader, clearFacetFilters, describeCount, useLabelsWidth, type DialogRequest } from './ListParts';

const PAGE = 100;
const MAX_LIMIT = 500;



interface ListState {
  readonly key: string;
  readonly items: readonly EntrySummary[];
  readonly total: number;
  readonly loading: boolean;
  readonly loadingMore: boolean;
  readonly error: AppError | null;
  /** Entries in the date range ignoring facet filters, for "12 of 41". */
  readonly rangeTotal: number | null;
}

const EMPTY: ListState = { key: '', items: [], total: 0, loading: true, loadingMore: false, error: null, rangeTotal: null };

export interface EntryListProps {
  readonly onAction: (action: DialogRequest) => void;
  readonly onNewEntry: () => void;
}

export function EntryList({ onAction, onNewEntry }: EntryListProps): JSX.Element {
  const currentFilters = filters.value;
  const order = view.value.order;
  const version = dataVersion.value;
  const key = JSON.stringify([currentFilters, order]);
  const [state, setState] = useState<ListState>(EMPTY);
  const [reload, setReload] = useState(0);
  const stateRef = useRef(state);
  stateRef.current = state;
  const scroller = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  /** Bumped by every reload; a page fetched for an older generation is dropped. */
  const generation = useRef(0);
  const moreController = useRef<AbortController | null>(null);
  const labels = useLabelsWidth(scroller);

  // First page, or a reload of what is loaded after entries changed (scroll position stays).
  useEffect(() => {
    const controller = new AbortController();
    generation.current += 1;
    // A reload replaces every loaded row, so a page still loading for the old rows is useless.
    moreController.current?.abort();
    moreController.current = null;
    const previous = stateRef.current;
    const sameView = previous.key === key;
    const saved = sameView ? undefined : savedScroll.get(key);
    const limit = Math.min(MAX_LIMIT, Math.max(PAGE, sameView ? previous.items.length : (saved?.loaded ?? 0)));
    setState(sameView ? { ...previous, loading: true } : { ...EMPTY, key });
    if (!sameView) scroller.current?.scrollTo({ top: 0 });
    const load = async (): Promise<void> => {
      try {
        const page = await api().listEntries({ filters: currentFilters, order, limit, offset: 0 }, controller.signal);
        let rangeTotal: number | null = null;
        if (facetFilterCount.value > 0) {
          const range: { from?: string; to?: string } = {};
          if (currentFilters.from !== undefined) range.from = currentFilters.from;
          if (currentFilters.to !== undefined) range.to = currentFilters.to;
          rangeTotal = (await api().listEntries({ filters: range, order, limit: 1, offset: 0 }, controller.signal)).total;
        }
        refreshSelected(page.items);
        setState({ key, items: page.items, total: page.total, loading: false, loadingMore: false, error: null, rangeTotal });
        if (saved !== undefined) {
          window.requestAnimationFrame(() => {
            if (scroller.current !== null) scroller.current.scrollTop = saved.top;
          });
        }
      } catch (error) {
        if (isAbort(error)) return;
        setState((s) => ({ ...s, key, loading: false, loadingMore: false, error: toAppError(error, 'Load entries') }));
      }
    };
    void load();
    return () => {
      controller.abort();
    };
    // currentFilters and order are captured by `key`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version, reload]);

  const loadMore = async (): Promise<void> => {
    const current = stateRef.current;
    if (current.loading || current.loadingMore || current.items.length >= current.total) return;
    const ownGeneration = generation.current;
    const controller = new AbortController();
    moreController.current = controller;
    setState({ ...current, loadingMore: true });
    try {
      const page = await api().listEntries(
        { filters: currentFilters, order, limit: PAGE, offset: current.items.length },
        controller.signal,
      );
      if (generation.current !== ownGeneration) return;
      setState((s) => {
        if (s.key !== current.key) return s;
        const known = new Set(s.items.map((item) => item.id));
        return { ...s, items: [...s.items, ...page.items.filter((item) => !known.has(item.id))], total: page.total, loadingMore: false };
      });
    } catch (error) {
      if (isAbort(error) || generation.current !== ownGeneration) return;
      setState((s) => ({ ...s, loadingMore: false, error: toAppError(error, 'Load more entries') }));
    } finally {
      if (moreController.current === controller) moreController.current = null;
    }
  };
  const loadMoreRef = useRef(loadMore);
  loadMoreRef.current = loadMore;

  // Remember where the reader was when this view goes away (page switch, search).
  useEffect(() => {
    const element = scroller.current;
    return () => {
      const current = stateRef.current;
      if (element !== null && current.key !== '') savedScroll.set(current.key, { top: element.scrollTop, loaded: current.items.length });
    };
  }, []);

  useEffect(() => {
    const target = sentinel.current;
    const root = scroller.current;
    if (target === null || root === null) return undefined;
    const observer = new IntersectionObserver(
      (records) => {
        if (records.some((record) => record.isIntersecting)) void loadMoreRef.current();
      },
      { root, rootMargin: '600px 0px' },
    );
    observer.observe(target);
    return () => {
      observer.disconnect();
    };
  }, []);

  const items = state.items;
  const roving = useRovingRow(items);
  const context: RowsContext = useMemo(
    () => ({
      visible: items,
      onOpen: (entry: EntrySummary) => {
        showEntry(entry.id, contextFor(items, order === 'desc', currentFilters, items.length >= state.total));
      },
    }),
    [items, order, currentFilters, state.total],
  );

  const range = bounds.value;
  const filterCount = facetFilterCount.value;
  const rangeText = describeBounds(range);
  const headText =
    state.loading && items.length === 0 ? (
      'Loading entries'
    ) : filterCount > 0 && state.rangeTotal !== null ? (
      <>
        <span class="mono">{state.total}</span> of {plural(state.rangeTotal, 'entry', 'entries')} match {plural(filterCount, 'filter')}
        <span class="muted"> · {rangeText}</span>
      </>
    ) : (
      <>
        <span class="mono">{describeCount(items.length, state.total)}</span>
        <span class="muted"> · {rangeText}</span>
      </>
    );

  return (
    <section class="list-area" aria-label="Entries">
      <ListHead
        visible={items}
        onAction={onAction}
        busy={state.loading && items.length > 0}
        actions={
          filterCount > 0 ? (
            <button type="button" class="link-button list-head-action" onClick={clearFacetFilters} data-testid="clear-filters">
              Clear filters
            </button>
          ) : undefined
        }
      >
        {headText}
      </ListHead>
      <ListNotice />
      {state.error !== null && (
        <div class="list-error">
          <ErrorNotice
            error={state.error}
            onRetry={() => {
              setReload((n) => n + 1);
            }}
          />
        </div>
      )}
      <div class="list-scroll" ref={scroller} data-testid="list-scroll">
        <div class="entry-table" role="grid" aria-label="Entries" aria-rowcount={state.total} aria-multiselectable="true">
          {items.length > 0 && (
            <TableHeader
              visible={items}
              labelsWidth={labels.width}
              onLabelsWidth={labels.setWidth}
              onResetWidth={labels.reset}
              sortable
            />
          )}
          {items.map((entry, index) => (
            <EntryRow
              key={entry.id}
              entry={entry}
              context={context}
              continuesDay={index > 0 && items[index - 1]?.entry_date === entry.entry_date}
              active={entry.id === roving.activeId}
              onActive={roving.setActive}
            />
          ))}
        </div>
        {!state.loading && state.error === null && items.length === 0 && (
          <ListEmpty filterCount={filterCount} rangeText={rangeText} allDates={range.from === null && range.to === null} onNewEntry={onNewEntry} />
        )}
        <div class="list-foot" ref={sentinel}>
          {items.length > 0 && items.length < state.total && (
            <button type="button" class="button button-small" disabled={state.loadingMore} onClick={() => void loadMore()}>
              {state.loadingMore ? 'Loading' : `Load more (${items.length} of ${state.total})`}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function ListEmpty(props: {
  readonly filterCount: number;
  readonly rangeText: string;
  readonly allDates: boolean;
  readonly onNewEntry: () => void;
}): JSX.Element {
  if (props.filterCount > 0) {
    return (
      <EmptyState
        action={
          <button type="button" class="button" onClick={clearFacetFilters}>
            Clear filters
          </button>
        }
      >
        No entries in {props.rangeText} match these filters.
      </EmptyState>
    );
  }
  if (props.allDates) {
    return (
      <EmptyState
        action={
          <button type="button" class="button button-primary" onClick={props.onNewEntry}>
            New entry
          </button>
        }
      >
        Entries you add appear here, newest first. Each one is queued for clean-up, a summary and extraction.
      </EmptyState>
    );
  }
  return (
    <EmptyState
      action={
        <button
          type="button"
          class="button"
          onClick={() => {
            updateView({ range: 'all' });
          }}
        >
          Show all dates
        </button>
      }
    >
      No entries from {props.rangeText}.
    </EmptyState>
  );
}
