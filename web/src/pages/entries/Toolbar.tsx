/** Entries toolbar: date range, facet filters, search; the primary action sits at its right end. */

import type { JSX, RefObject } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import type { FacetValue } from '../../api/types';
import { ErrorNotice } from '../../components/ErrorNotice';
import { Icon } from '../../components/Icon';
import { Popover, hidePopover } from '../../components/Popover';
import { WIDE, useMediaQuery } from '../../hooks/useMediaQuery';
import { describeBounds, isIsoDate, presetBounds, type RangeKey } from '../../lib/dates';
import { facetState, loadFacets } from '../../state/facets';
import { bounds, facetFilterCount, today, updateView, view, type EntriesView, type SearchMode } from '../../state/view';

const RANGE_LABELS: Readonly<Record<RangeKey, string>> = {
  today: 'Today',
  week: 'Week',
  month: 'Month',
  year: 'Year',
  all: 'All',
  custom: 'Custom',
};

const RANGE_LONG: Readonly<Record<RangeKey, string>> = {
  today: 'Today',
  week: 'Past week',
  month: 'Past month',
  year: 'Past year',
  all: 'All dates',
  custom: 'Custom range',
};

const PRESETS = ['today', 'week', 'month', 'year', 'all'] as const;

function presetTitle(key: (typeof PRESETS)[number]): string {
  return `${RANGE_LONG[key]}: ${describeBounds(presetBounds(key, today.value))}`;
}

function CustomRange({ onDone }: { readonly onDone: () => void }): JSX.Element {
  const current = view.value;
  const initial = current.range === 'custom' ? current.custom : bounds.value;
  const [from, setFrom] = useState(initial.from ?? '');
  const [to, setTo] = useState(initial.to ?? today.value);
  const fromValid = from === '' || isIsoDate(from);
  const toValid = to === '' || isIsoDate(to);
  const ordered = from === '' || to === '' || !fromValid || !toValid || from <= to;
  const valid = fromValid && toValid && ordered && (from !== '' || to !== '');

  return (
    <form
      class="custom-range"
      onSubmit={(event) => {
        event.preventDefault();
        if (!valid) return;
        updateView({ range: 'custom', custom: { from: from === '' ? null : from, to: to === '' ? null : to } });
        onDone();
      }}
    >
      <div class="field">
        <label class="field-label" for="range-from">
          From
        </label>
        <input
          id="range-from"
          class={fromValid ? 'input input-mono' : 'input input-mono is-invalid'}
          placeholder="YYYY-MM-DD"
          inputMode="numeric"
          value={from}
          data-testid="range-from"
          onInput={(e) => {
            setFrom(e.currentTarget.value.trim());
          }}
        />
      </div>
      <div class="field">
        <label class="field-label" for="range-to">
          To
        </label>
        <input
          id="range-to"
          class={toValid ? 'input input-mono' : 'input input-mono is-invalid'}
          placeholder="YYYY-MM-DD"
          inputMode="numeric"
          value={to}
          data-testid="range-to"
          onInput={(e) => {
            setTo(e.currentTarget.value.trim());
          }}
        />
      </div>
      <p class={valid || (from === '' && to === '') ? 'field-hint' : 'field-hint is-invalid'}>
        {!fromValid || !toValid ? 'Write dates as YYYY-MM-DD.' : !ordered ? 'From must not be after To.' : 'Both ends are included. Leave one empty for an open range.'}
      </p>
      <div class="popover-foot">
        <button type="submit" class="button button-primary button-small" disabled={!valid} data-testid="range-apply">
          Apply range
        </button>
      </div>
    </form>
  );
}

function RangeControl(): JSX.Element {
  const wide = useMediaQuery(WIDE);
  const current = view.value.range;
  const customAnchor = useRef<HTMLButtonElement>(null);
  const menuAnchor = useRef<HTMLButtonElement>(null);
  const customLabel = current === 'custom' ? describeBounds(view.value.custom) : RANGE_LABELS.custom;

  if (!wide) {
    return (
      <>
        <button
          type="button"
          class="button range-menu-button"
          ref={menuAnchor}
          popovertarget="range-menu"
          title={`Date range: ${describeBounds(bounds.value)}`}
          data-testid="range-menu-button"
        >
          <span>{current === 'custom' ? customLabel : RANGE_LONG[current]}</span>
          <Icon name="chevron-down" size={14} />
        </button>
        <Popover id="range-menu" anchor={menuAnchor} label="Date range" class="menu range-popover">
          <div class="menu-title">Date range</div>
          {PRESETS.map((key) => (
            <button
              type="button"
              key={key}
              class={current === key ? 'menu-item is-on' : 'menu-item'}
              aria-pressed={current === key}
              onClick={() => {
                updateView({ range: key });
                hidePopover('range-menu');
              }}
            >
              <span>{RANGE_LONG[key]}</span>
              <span class="menu-item-meta mono">{describeBounds(presetBounds(key, today.value))}</span>
            </button>
          ))}
          <div class="menu-section">
            <div class="menu-title">Custom range</div>
            <CustomRange
              onDone={() => {
                hidePopover('range-menu');
              }}
            />
          </div>
        </Popover>
      </>
    );
  }

  return (
    <div class="segmented" role="group" aria-label="Date range" data-testid="range-control">
      {PRESETS.map((key) => (
        <button
          type="button"
          key={key}
          class={current === key ? 'segment is-on' : 'segment'}
          aria-pressed={current === key}
          title={presetTitle(key)}
          data-testid={`range-${key}`}
          onClick={() => {
            updateView({ range: key });
          }}
        >
          {RANGE_LABELS[key]}
        </button>
      ))}
      <button
        type="button"
        ref={customAnchor}
        class={current === 'custom' ? 'segment is-on mono-when-on' : 'segment'}
        aria-pressed={current === 'custom'}
        popovertarget="range-custom"
        title="Pick a custom date range"
        data-testid="range-custom"
      >
        {customLabel}
      </button>
      <Popover id="range-custom" anchor={customAnchor} label="Custom date range" align="end" class="range-popover">
        <CustomRange
          onDone={() => {
            hidePopover('range-custom');
          }}
        />
      </Popover>
    </div>
  );
}

type FacetKind = 'tags' | 'projects' | 'skills';
const FACET_LABELS: Readonly<Record<FacetKind, string>> = { tags: 'Tags', projects: 'Projects', skills: 'Skills' };
const FACET_HINTS: Readonly<Record<FacetKind, string>> = {
  tags: 'Your own tags',
  projects: 'Projects found by extraction',
  skills: 'Skills and technologies found by extraction',
};

function viewValues(current: EntriesView, kind: FacetKind): readonly string[] {
  return kind === 'tags' ? current.tags : kind === 'projects' ? current.projects : current.skills;
}

function setValues(kind: FacetKind, values: readonly string[]): void {
  updateView(kind === 'tags' ? { tags: values } : kind === 'projects' ? { projects: values } : { skills: values });
}

/**
 * Facet values as a multi-select list: plain click selects one value alone,
 * Ctrl/Cmd-click adds or removes one, Shift-click selects a range. A touch tap
 * toggles, since touch has no modifier keys. Enter/Space mirror the clicks.
 */
function FacetList({ kind, values }: { readonly kind: FacetKind; readonly values: readonly FacetValue[] }): JSX.Element {
  const selected = viewValues(view.value, kind);
  const selectedLower = useMemo(() => new Set(selected.map((v) => v.toLowerCase())), [selected]);
  const [query, setQuery] = useState('');
  /** The option holding the list's single tab stop. */
  const [activeIndex, setActiveIndex] = useState(0);
  const anchor = useRef<string | null>(null);
  const pointerType = useRef<string>('mouse');
  const shown = query.trim() === '' ? values : values.filter((v) => v.value.toLowerCase().includes(query.trim().toLowerCase()));

  const choose = (value: string, mode: 'only' | 'toggle' | 'range'): void => {
    const isOn = selectedLower.has(value.toLowerCase());
    if (mode === 'toggle') {
      setValues(kind, isOn ? selected.filter((v) => v.toLowerCase() !== value.toLowerCase()) : [...selected, value]);
      anchor.current = value;
      return;
    }
    if (mode === 'range' && anchor.current !== null) {
      const names = shown.map((v) => v.value);
      const a = names.indexOf(anchor.current);
      const b = names.indexOf(value);
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a <= b ? [a, b] : [b, a];
        const range = names.slice(lo, hi + 1);
        const merged = [...selected.filter((v) => !range.some((r) => r.toLowerCase() === v.toLowerCase())), ...range];
        setValues(kind, merged);
        return;
      }
    }
    setValues(kind, isOn && selected.length === 1 ? [] : [value]);
    anchor.current = value;
  };

  const modeFor = (event: MouseEvent | KeyboardEvent): 'only' | 'toggle' | 'range' =>
    event.shiftKey ? 'range' : event.ctrlKey || event.metaKey ? 'toggle' : 'only';

  return (
    <div class="facet-list">
      {values.length > 8 && (
        <input
          class="input input-small"
          type="search"
          placeholder={`Find ${FACET_LABELS[kind].toLowerCase()}`}
          aria-label={`Find ${FACET_LABELS[kind].toLowerCase()}`}
          value={query}
          onInput={(e) => {
            setQuery(e.currentTarget.value);
          }}
        />
      )}
      <ul class="facet-options" role="listbox" aria-multiselectable="true" aria-label={FACET_LABELS[kind]}>
        {shown.map((facet, index) => {
          const on = selectedLower.has(facet.value.toLowerCase());
          return (
            <li
              key={facet.value}
              role="option"
              aria-selected={on}
              tabIndex={index === Math.min(activeIndex, shown.length - 1) ? 0 : -1}
              onFocus={() => {
                setActiveIndex(index);
              }}
              class={on ? 'facet-option is-on' : 'facet-option'}
              data-testid="facet-option"
              onPointerDown={(event) => {
                pointerType.current = event.pointerType;
              }}
              onClick={(event) => {
                choose(facet.value, pointerType.current === 'touch' ? 'toggle' : modeFor(event));
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  choose(facet.value, modeFor(event));
                } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                  event.preventDefault();
                  const list = event.currentTarget.parentElement;
                  const options = list === null ? [] : [...list.querySelectorAll<HTMLElement>('[role=option]')];
                  const target =
                    event.key === 'Home'
                      ? options[0]
                      : event.key === 'End'
                        ? options.at(-1)
                        : options[index + (event.key === 'ArrowDown' ? 1 : -1)];
                  target?.focus();
                }
              }}
            >
              <span class="facet-value">{facet.value}</span>
              <span class="facet-count mono">{facet.count}</span>
            </li>
          );
        })}
        {shown.length === 0 && <li class="facet-empty">{values.length === 0 ? `No ${FACET_LABELS[kind].toLowerCase()} yet.` : 'No match.'}</li>}
      </ul>
    </div>
  );
}

function FacetPanel({ kind }: { readonly kind: FacetKind }): JSX.Element {
  const { facets, error } = facetState.value;
  const selected = viewValues(view.value, kind);
  return (
    <div class="facet-panel">
      <div class="facet-panel-head">
        <span class="menu-title" title={FACET_HINTS[kind]}>
          {FACET_LABELS[kind]}
        </span>
        {selected.length > 0 && (
          <button
            type="button"
            class="link-button"
            onClick={() => {
              setValues(kind, []);
            }}
          >
            Clear {selected.length}
          </button>
        )}
      </div>
      {error !== null && facets === null ? (
        <ErrorNotice error={error} compact onRetry={() => void loadFacets()} />
      ) : facets === null ? (
        <p class="muted">Loading</p>
      ) : (
        <FacetList kind={kind} values={facets[kind]} />
      )}
      <p class="facet-help">Click picks one. Ctrl or Cmd adds more, Shift picks a range.</p>
    </div>
  );
}

function FacetButton({ kind }: { readonly kind: FacetKind }): JSX.Element {
  const anchor = useRef<HTMLButtonElement>(null);
  const count = viewValues(view.value, kind).length;
  const id = `facet-${kind}`;
  return (
    <>
      <button
        type="button"
        ref={anchor}
        class={count > 0 ? 'button filter-button is-active' : 'button filter-button'}
        popovertarget={id}
        title={count > 0 ? `${FACET_LABELS[kind]}: ${viewValues(view.value, kind).join(', ')}` : `Filter by ${FACET_HINTS[kind].toLowerCase()}`}
        data-testid={`filter-${kind}`}
      >
        <span>{FACET_LABELS[kind]}</span>
        <span class="filter-badge" aria-label={count > 0 ? `${count} selected` : 'no filter'}>
          <Icon name="filter" size={14} />
          {count > 0 && <span class="filter-count mono">{count}</span>}
        </span>
      </button>
      <Popover id={id} anchor={anchor} label={`Filter by ${FACET_LABELS[kind]}`} class="facet-popover">
        <FacetPanel kind={kind} />
      </Popover>
    </>
  );
}

function FiltersSheetButton(): JSX.Element {
  const anchor = useRef<HTMLButtonElement>(null);
  const count = facetFilterCount.value;
  return (
    <>
      <button
        type="button"
        ref={anchor}
        class={count > 0 ? 'button filter-button is-active' : 'button filter-button'}
        popovertarget="facet-all"
        title="Filter by tags, projects or skills"
        data-testid="filter-all"
      >
        <span>Filters</span>
        <span class="filter-badge">
          <Icon name="filter" size={14} />
          {count > 0 && <span class="filter-count mono">{count}</span>}
        </span>
      </button>
      <Popover id="facet-all" anchor={anchor} label="Filters" class="facet-popover facet-popover-all">
        <FacetPanel kind="tags" />
        <FacetPanel kind="projects" />
        <FacetPanel kind="skills" />
      </Popover>
    </>
  );
}

const MODE_HINTS: Readonly<Record<SearchMode, string>> = {
  keyword: 'Keyword: exact phrase, then contains, close spelling, ranked words, and similar meaning',
  relevance: 'Relevance: rank entries by how well they answer a question or topic, with matching passages; good for picking entries to export',
};

export function SearchBox({ inputRef }: { readonly inputRef: RefObject<HTMLInputElement> }): JSX.Element {
  const current = view.value;
  const [draft, setDraft] = useState(current.q);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  // Follow outside changes of the query (back navigation, clearing) without touching a draft that already matches.
  useEffect(() => {
    if (draftRef.current.trim() !== current.q) setDraft(current.q);
  }, [current.q]);

  const commit = (value: string): void => {
    if (value !== view.value.q) updateView({ q: value });
  };
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const value = draft.trim();
      if (value !== view.value.q) updateView({ q: value });
    }, 250);
    return () => {
      window.clearTimeout(timer);
    };
  }, [draft]);

  return (
    <div class="search-box" role="search">
      <label class="search-field">
        <Icon name="search" size={16} class="search-icon" />
        <span class="visually-hidden">Search entries</span>
        <input
          ref={inputRef}
          class="search-input"
          type="search"
          placeholder={current.mode === 'relevance' ? 'Find entries relevant to...' : 'Search entries'}
          title="Search entries (/); up to 500 characters"
          maxLength={500}
          value={draft}
          enterKeyHint="search"
          data-testid="search-input"
          onInput={(event) => {
            setDraft(event.currentTarget.value);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') commit(draft.trim());
            if (event.key === 'Escape' && draft !== '') {
              event.preventDefault();
              setDraft('');
              commit('');
            }
          }}
        />
        {draft !== '' && (
          <button
            type="button"
            class="icon-button icon-button-small search-clear"
            title="Clear the search (Esc)"
            aria-label="Clear the search"
            data-testid="search-clear"
            onClick={() => {
              setDraft('');
              commit('');
              inputRef.current?.focus();
            }}
          >
            <Icon name="close" size={14} />
          </button>
        )}
      </label>
      <div class="segmented segmented-small search-mode" role="group" aria-label="Search mode">
        {(['keyword', 'relevance'] as const).map((mode) => (
          <button
            type="button"
            key={mode}
            class={current.mode === mode ? 'segment is-on' : 'segment'}
            aria-pressed={current.mode === mode}
            title={MODE_HINTS[mode]}
            data-testid={`mode-${mode}`}
            onClick={() => {
              updateView({ mode });
            }}
          >
            {mode === 'keyword' ? 'Keyword' : 'Relevance'}
          </button>
        ))}
      </div>
    </div>
  );
}

export interface ToolbarProps {
  readonly onNewEntry: () => void;
  readonly searchRef: RefObject<HTMLInputElement>;
}

export function Toolbar({ onNewEntry, searchRef }: ToolbarProps): JSX.Element {
  const wide = useMediaQuery(WIDE);
  return (
    <div class="toolbar" data-testid="toolbar">
      <div class="toolbar-row">
        <RangeControl />
        {wide ? (
          <div class="filter-group">
            <FacetButton kind="tags" />
            <FacetButton kind="projects" />
            <FacetButton kind="skills" />
          </div>
        ) : (
          <FiltersSheetButton />
        )}
        {wide && <SearchBox inputRef={searchRef} />}
        <span class="toolbar-spacer" />
        <button type="button" class="button button-primary" title="Write or record a new entry (N)" data-testid="new-entry" onClick={onNewEntry}>
          <Icon name="plus" size={16} />
          <span>New entry</span>
        </button>
      </div>
      {!wide && (
        <div class="toolbar-row">
          <SearchBox inputRef={searchRef} />
        </div>
      )}
    </div>
  );
}
