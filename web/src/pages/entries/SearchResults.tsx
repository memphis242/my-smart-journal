/**
 * Search results. Keyword mode streams tiers 1-5 and renders each tier as its
 * line arrives; relevance mode ranks entries for a question with their best
 * passages (POST /retrieve), for building an export or compile selection.
 */

import type { JSX } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { AppError, isAbort, toAppError } from '../../api/errors';
import type { EntryFilters, EntrySummary, RetrieveResult, SearchHit, SearchTier, SearchTierEvent, Unavailable } from '../../api/types';
import { SEARCH_TIERS } from '../../api/types';
import { ErrorNotice } from '../../components/ErrorNotice';
import { StatusMarker } from '../../components/StatusMarker';
import { describeBounds } from '../../lib/dates';
import { formatScore, plural } from '../../lib/format';
import { api, dataVersion } from '../../state/app';
import { chronologicalContext, showEntry } from '../../state/entry-modal';
import { refreshSelected } from '../../state/selection';
import { bounds, filters, view } from '../../state/view';
import { EntryRow, type RowsContext } from './EntryRows';
import { EmptyState, ListHead, TableHeader, useLabelsWidth, type DialogRequest } from './ListParts';

export const TIER_LABELS: Readonly<Record<SearchTier, string>> = {
  1: 'Exact phrase',
  2: 'Contains',
  3: 'Close spelling',
  4: 'Ranked words',
  5: 'Similar meaning',
};

const TIER_HINTS: Readonly<Record<SearchTier, string>> = {
  1: 'The exact phrase, case-sensitive',
  2: 'The words as written, ignoring case',
  3: 'Words spelled almost the same',
  4: 'Full-text ranking of any of the words, newer entries first on ties',
  5: 'Passages with a similar meaning, from the local sentence-embedding model',
};

/** Splits text around case-insensitive occurrences of the query words, for highlighting. */
export function highlight(text: string, query: string): JSX.Element {
  const words = query
    .split(/\s+/)
    .map((word) => word.replace(/[^\p{L}\p{N}_-]/gu, ''))
    .filter((word) => word.length >= 2);
  if (words.length === 0) return <>{text}</>;
  const pattern = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'giu');
  const parts = text.split(pattern);
  return (
    <>
      {parts.map((part, index) => (index % 2 === 1 ? <mark key={index}>{part}</mark> : part))}
    </>
  );
}

const DEBOUNCE_MS = 280;

/** Summaries for search hits: from the hit itself, or one list request per filter set. */
function useEntryResolver(currentFilters: EntryFilters, version: number): {
  readonly entries: ReadonlyMap<string, EntrySummary>;
  readonly need: (hits: readonly SearchHit[]) => void;
} {
  const [entries, setEntries] = useState<ReadonlyMap<string, EntrySummary>>(new Map());
  const known = useRef(new Map<string, EntrySummary>());
  const listLoaded = useRef<Promise<void> | null>(null);
  const key = JSON.stringify(currentFilters);
  const keyRef = useRef(key);

  useEffect(() => {
    keyRef.current = key;
    listLoaded.current = null;
    known.current = new Map();
    setEntries(new Map());
  }, [key, version]);

  const add = (more: readonly EntrySummary[]): void => {
    if (more.length === 0) return;
    for (const entry of more) known.current.set(entry.id, entry);
    setEntries(new Map(known.current));
  };

  const need = (hits: readonly SearchHit[]): void => {
    add(hits.flatMap((hit) => (hit.entry === null ? [] : [hit.entry])));
    const missing = hits.filter((hit) => hit.entry === null).map((hit) => hit.entry_id);
    if (missing.length === 0) return;
    const requestKey = keyRef.current;
    listLoaded.current ??= api()
      .listEntries({ filters: currentFilters, order: 'desc', limit: 500, offset: 0 })
      .then((page) => {
        if (keyRef.current === requestKey) add(page.items);
      });
    void listLoaded.current
      .then(async () => {
        // Corpora larger than one page: fetch the remaining hits one by one.
        const rest = missing.filter((id) => !known.current.has(id)).slice(0, 40);
        if (rest.length === 0) return;
        const details = await Promise.all(rest.map((id) => api().getEntry(id).catch(() => null)));
        if (keyRef.current === requestKey) add(details.filter((d): d is NonNullable<typeof d> => d !== null));
      })
      .catch(() => {
        // Rows without a summary still open by id; the detail view reports its own errors.
        listLoaded.current = null;
      });
  };

  return { entries, need };
}

interface KeywordState {
  readonly tiers: ReadonlyMap<SearchTier, SearchTierEvent>;
  readonly done: boolean;
  readonly error: AppError | null;
  readonly elapsedMs: number | null;
}

const KEYWORD_EMPTY: KeywordState = { tiers: new Map(), done: false, error: null, elapsedMs: null };

function TierProgress({ state, searching }: { readonly state: KeywordState; readonly searching: boolean }): JSX.Element {
  return (
    <ol class="tier-progress" aria-label="Search tiers" data-testid="tier-progress">
      {SEARCH_TIERS.map((tier) => {
        const event = state.tiers.get(tier);
        const status = event === undefined ? (searching ? 'pending' : 'idle') : event.unavailable !== null && event.unavailable.code !== 'EMBEDDINGS_PENDING' ? 'unavailable' : 'done';
        const count = event?.results.length ?? null;
        const title = `${TIER_LABELS[tier]}: ${TIER_HINTS[tier]}${
          event === undefined ? (searching ? ' (searching)' : '') : `. ${plural(event.results.length, 'new result')}, ${event.elapsed_ms.toFixed(1)} ms`
        }`;
        return (
          <li class={`tier-step is-${status}`} key={tier} title={title} data-tier={tier} data-state={status}>
            <span class="tier-bar" />
            <span class="tier-name">{TIER_LABELS[tier]}</span>
            <span class="tier-count mono">{count ?? ''}</span>
          </li>
        );
      })}
    </ol>
  );
}

function UnavailableNote({ unavailable }: { readonly unavailable: Unavailable }): JSX.Element {
  const pending = unavailable.code === 'EMBEDDINGS_PENDING';
  return (
    <p class={pending ? 'inline-note' : 'inline-note is-attention'} data-testid="semantic-unavailable" title={unavailable.detail}>
      <StatusMarker kind={pending ? 'queued' : 'attention'} label={pending ? 'Partly available' : 'Unavailable'} />
      <span>{pending ? 'Similar meaning is partial' : 'Similar meaning is unavailable'}</span>
      <span class="note-detail">{unavailable.detail}</span>
      <code class="error-code">{unavailable.code}</code>
    </p>
  );
}

export interface SearchResultsProps {
  readonly onAction: (action: DialogRequest) => void;
}

export function SearchResults({ onAction }: SearchResultsProps): JSX.Element {
  const { q, mode } = view.value;
  return mode === 'relevance' ? <RelevanceResults q={q} onAction={onAction} /> : <KeywordResults q={q} onAction={onAction} />;
}

function KeywordResults({ q, onAction }: { readonly q: string; readonly onAction: (action: DialogRequest) => void }): JSX.Element {
  const currentFilters = filters.value;
  const version = dataVersion.value;
  const [state, setState] = useState<KeywordState>(KEYWORD_EMPTY);
  const [searching, setSearching] = useState(false);
  const resolver = useEntryResolver(currentFilters, version);
  const needRef = useRef(resolver.need);
  needRef.current = resolver.need;
  const scroller = useRef<HTMLDivElement>(null);
  const labels = useLabelsWidth(scroller);
  const key = JSON.stringify([q, currentFilters]);

  useEffect(() => {
    const controller = new AbortController();
    setState(KEYWORD_EMPTY);
    setSearching(true);
    const timer = window.setTimeout(() => {
      api()
        .search(
          { q, filters: currentFilters, tiers: SEARCH_TIERS },
          (event) => {
            if (event.type === 'tier') {
              needRef.current(event.results);
              setState((s) => ({ ...s, tiers: new Map(s.tiers).set(event.tier, event) }));
            } else {
              setState((s) => ({
                ...s,
                done: true,
                elapsedMs: event.elapsed_ms,
                error:
                  event.error === null
                    ? null
                    : new AppError({
                        code: event.error.code ?? 'SEARCH_FAILED',
                        message: `Search stopped early: ${event.error.detail}`,
                        hint: 'Results above are from the tiers that finished. Check the service log, then search again.',
                        details: event.error.status === null ? [] : [{ label: 'Status', value: String(event.error.status) }],
                      }),
              }));
            }
          },
          controller.signal,
        )
        .catch((error: unknown) => {
          if (isAbort(error)) return;
          setState((s) => ({ ...s, error: toAppError(error, 'Search') }));
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // currentFilters is captured by `key`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version]);

  const groups = SEARCH_TIERS.flatMap((tier) => {
    const event = state.tiers.get(tier);
    return event === undefined || event.results.length === 0 ? [] : [{ tier, event }];
  });
  const hits = groups.flatMap((group) => group.event.results);
  const visible = useMemo(
    () => hits.flatMap((hit) => {
      const entry = resolver.entries.get(hit.entry_id);
      return entry === undefined ? [] : [entry];
    }),
    [hits, resolver.entries],
  );
  useEffect(() => {
    refreshSelected(visible);
  }, [visible]);

  const context: RowsContext = useMemo(
    () => ({
      visible,
      onOpen: (entry: EntrySummary) => {
        showEntry(entry.id, chronologicalContext(visible));
      },
    }),
    [visible],
  );
  const semantic = state.tiers.get(5);
  const total = hits.length;

  return (
    <section class="list-area" aria-label="Search results" data-testid="search-results">
      <ListHead visible={visible} onAction={onAction} busy={searching} density={false}>
        {state.done || total > 0 ? (
          <>
            <span class="mono">{plural(total, 'result')}</span>
            <span class="muted"> for "{q}" · {describeBounds(bounds.value)}</span>
            {state.elapsedMs !== null && <span class="muted mono"> · {state.elapsedMs.toFixed(0)} ms</span>}
          </>
        ) : (
          <>Searching for "{q}"</>
        )}
      </ListHead>
      <div class="results-meta">
        <TierProgress state={state} searching={searching && !state.done} />
        {semantic?.unavailable != null && <UnavailableNote unavailable={semantic.unavailable} />}
      </div>
      {state.error !== null && (
        <div class="list-error">
          <ErrorNotice error={state.error} />
        </div>
      )}
      <div class="list-scroll" ref={scroller} data-testid="list-scroll">
        <div class="entry-table" role="table" aria-label="Search results">
          {groups.length > 0 && (
            <TableHeader
              visible={visible}
              labelsWidth={labels.width}
              onLabelsWidth={labels.setWidth}
              onResetWidth={labels.reset}
              sortable={false}
              figureLabel="Score"
            />
          )}
          {groups.map(({ tier, event }) => (
            <div class="tier-group" role="rowgroup" key={tier} data-testid="tier-group" data-tier={tier}>
              <div class="tier-head" role="row" title={TIER_HINTS[tier]}>
                <span class="tier-head-name">{TIER_LABELS[tier]}</span>
                <span class="tier-head-count mono">{event.results.length}</span>
              </div>
              {event.results.map((hit) => {
                const entry = resolver.entries.get(hit.entry_id);
                if (entry === undefined) return <PendingRow key={hit.entry_id} hit={hit} q={q} tier={tier} />;
                return (
                  <EntryRow
                    key={hit.entry_id}
                    entry={entry}
                    context={context}
                    figure={formatScore(hit.score)}
                    figureTitle={`Score within ${TIER_LABELS[tier]}`}
                    hit={<HitText hit={hit} q={q} tier={tier} />}
                  />
                );
              })}
            </div>
          ))}
        </div>
        {state.done && total === 0 && state.error === null && (
          <EmptyState>
            No entries in {describeBounds(bounds.value)} match "{q}". Try fewer words, or switch to Relevance to rank by meaning.
          </EmptyState>
        )}
      </div>
    </section>
  );
}

function HitText({ hit, q, tier }: { readonly hit: SearchHit; readonly q: string; readonly tier: SearchTier }): JSX.Element | null {
  if (tier === 5 && hit.passage !== null) return <span class="passage">{hit.passage}</span>;
  if (hit.snippet === null) return null;
  return <span class="snippet">{highlight(hit.snippet, q)}</span>;
}

function PendingRow({ hit, q, tier }: { readonly hit: SearchHit; readonly q: string; readonly tier: SearchTier }): JSX.Element {
  return (
    <div
      class="entry-row is-tall is-pending"
      role="row"
      tabIndex={0}
      data-row-id={hit.entry_id}
      onClick={() => {
        showEntry(hit.entry_id, { newestFirst: [hit.entry_id], previews: new Map(), filters: {}, complete: false });
      }}
    >
      <span class="cell cell-select" role="cell" />
      <span class="cell cell-status" role="cell" />
      <span class="cell cell-date" role="cell">
        <span class="date mono muted">loading</span>
      </span>
      <span class="cell cell-main" role="cell">
        <span class="row-hit">
          <HitText hit={hit} q={q} tier={tier} />
        </span>
      </span>
    </div>
  );
}

interface RelevanceState {
  readonly result: RetrieveResult | null;
  readonly error: AppError | null;
  readonly loading: boolean;
}

function RelevanceResults({ q, onAction }: { readonly q: string; readonly onAction: (action: DialogRequest) => void }): JSX.Element {
  const currentFilters = filters.value;
  const version = dataVersion.value;
  const [state, setState] = useState<RelevanceState>({ result: null, error: null, loading: true });
  const scroller = useRef<HTMLDivElement>(null);
  const labels = useLabelsWidth(scroller);
  const key = JSON.stringify([q, currentFilters]);

  useEffect(() => {
    const controller = new AbortController();
    setState((s) => ({ ...s, loading: true, error: null }));
    const timer = window.setTimeout(() => {
      api()
        .retrieve({ query: q, k: 30, filters: currentFilters }, controller.signal)
        .then((result) => {
          refreshSelected(result.items.map((item) => item.entry));
          setState({ result, error: null, loading: false });
        })
        .catch((error: unknown) => {
          if (isAbort(error)) return;
          setState({ result: null, error: toAppError(error, 'Find relevant entries'), loading: false });
        });
    }, DEBOUNCE_MS + 150);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // currentFilters is captured by `key`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version]);

  const result = state.result;
  const items = useMemo(() => result?.items ?? [], [result]);
  const visible = useMemo(() => items.map((item) => item.entry), [items]);
  const context: RowsContext = useMemo(
    () => ({
      visible,
      onOpen: (entry: EntrySummary) => {
        showEntry(entry.id, chronologicalContext(visible));
      },
    }),
    [visible],
  );

  return (
    <section class="list-area" aria-label="Relevant entries" data-testid="relevance-results">
      <ListHead visible={visible} onAction={onAction} busy={state.loading} density={false}>
        {state.result === null ? (
          <>Ranking entries for "{q}"</>
        ) : (
          <>
            <span class="mono">{plural(items.length, 'entry', 'entries')}</span>
            <span class="muted"> ranked for "{q}" · {describeBounds(bounds.value)}</span>
          </>
        )}
      </ListHead>
      {state.result?.unavailable != null && (
        <div class="results-meta">
          <UnavailableNote unavailable={state.result.unavailable} />
        </div>
      )}
      {state.error !== null && (
        <div class="list-error">
          <ErrorNotice error={state.error} />
        </div>
      )}
      <div class="list-scroll" ref={scroller} data-testid="list-scroll">
        <div class="entry-table" role="table" aria-label="Relevant entries">
          {items.length > 0 && (
            <TableHeader
              visible={visible}
              labelsWidth={labels.width}
              onLabelsWidth={labels.setWidth}
              onResetWidth={labels.reset}
              sortable={false}
              figureLabel="Score"
            />
          )}
          {items.map((item) => (
            <EntryRow
              key={item.entry.id}
              entry={item.entry}
              context={context}
              figure={formatScore(item.score)}
              figureTitle="Relevance: semantic and keyword ranks fused (reciprocal rank fusion), 0 to 1"
              hit={item.passages[0] === undefined ? undefined : <span class="passage">{item.passages[0]}</span>}
            />
          ))}
        </div>
        {state.result !== null && items.length === 0 && (
          <EmptyState>Nothing in {describeBounds(bounds.value)} looks relevant to "{q}".</EmptyState>
        )}
      </div>
    </section>
  );
}
