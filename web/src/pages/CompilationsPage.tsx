/** Compilations: material compiled from selected entries, newest first; each opens in a modal. */

import type { JSX } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { AppError, isAbort, toAppError } from '../api/errors';
import type { Compilation, CompileResult } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Dialog, DialogHeader } from '../components/Dialog';
import { ErrorNotice } from '../components/ErrorNotice';
import { Icon } from '../components/Icon';
import { StatusMarker, compilationMarker } from '../components/StatusMarker';
import { WIDE, useMediaQuery } from '../hooks/useMediaQuery';
import { copyText } from '../lib/clipboard';
import { formatDateTime } from '../lib/dates';
import { COMPILATION_STATUS_LABELS, plural } from '../lib/format';
import { api, bumpData, dataVersion } from '../state/app';
import { batchRequest, queueState, runBatchNow } from '../state/queue';
import { navigate } from '../state/route';
import { toast } from '../state/toasts';
import { updateView } from '../state/view';

type Tab = 'bullets' | 'stories' | 'topics' | 'threads';

const TABS: readonly { readonly tab: Tab; readonly label: string; readonly short: string }[] = [
  { tab: 'bullets', label: 'Resume bullets', short: 'Bullets' },
  { tab: 'stories', label: 'STAR stories', short: 'STAR' },
  { tab: 'topics', label: 'Technical topics', short: 'Topics' },
  { tab: 'threads', label: 'Open threads', short: 'Threads' },
];

function count(result: CompileResult, tab: Tab): number {
  switch (tab) {
    case 'bullets':
      return result.resume_bullets.length;
    case 'stories':
      return result.star_stories.length;
    case 'topics':
      return result.technical_topics.length;
    case 'threads':
      return result.open_threads.length;
  }
}

/** Plain text of one section, laid out for pasting into Word. */
export function sectionText(result: CompileResult, tab: Tab): string {
  switch (tab) {
    case 'bullets':
      return result.resume_bullets.map((bullet) => `• ${bullet.text}`).join('\n');
    case 'stories':
      return result.star_stories
        .map((story) =>
          [
            story.title,
            `Situation: ${story.situation}`,
            `Task: ${story.task}`,
            `Action: ${story.action}`,
            `Result: ${story.result}`,
            story.competencies.length > 0 ? `Competencies: ${story.competencies.join(', ')}` : null,
          ]
            .filter((line) => line !== null)
            .join('\n'),
        )
        .join('\n\n');
    case 'topics':
      return result.technical_topics.map((topic) => `${topic.topic}\n${topic.depth_notes}`).join('\n\n');
    case 'threads':
      return result.open_threads.map((thread) => `• ${thread}`).join('\n');
  }
}

function allText(compilation: Compilation, result: CompileResult): string {
  const head = `${compilation.purpose}, ${compilation.period}`;
  const sections = TABS.filter(({ tab }) => count(result, tab) > 0).map(({ tab, label }) => `${label}\n\n${sectionText(result, tab)}`);
  return [head, ...sections].join('\n\n\n');
}

/** Copies and confirms; resolves the error to show in place, or null. */
async function copy(text: string, what: string): Promise<AppError | null> {
  try {
    await copyText(text);
    toast(`Copied ${what}`);
    return null;
  } catch (error) {
    return toAppError(error, 'Copy');
  }
}

function Evidence({ dates, onDate }: { readonly dates: readonly string[]; readonly onDate: (date: string) => void }): JSX.Element | null {
  if (dates.length === 0) return null;
  return (
    <span class="evidence" title="Entries this is based on">
      {dates.map((date) => (
        <button
          type="button"
          key={date}
          class="evidence-date mono"
          title={`Show the entries of ${date}`}
          onClick={() => {
            onDate(date);
          }}
        >
          {date}
        </button>
      ))}
    </span>
  );
}

function ResultTab({ result, tab, onDate }: { readonly result: CompileResult; readonly tab: Tab; readonly onDate: (date: string) => void }): JSX.Element {
  switch (tab) {
    case 'bullets':
      return (
        <ul class="compile-list">
          {result.resume_bullets.map((bullet, index) => (
            <li key={index} class="compile-item">
              <p class="prose">{bullet.text}</p>
              <Evidence dates={bullet.evidence_dates} onDate={onDate} />
            </li>
          ))}
        </ul>
      );
    case 'stories':
      return (
        <div class="stories">
          {result.star_stories.map((story, index) => (
            <article key={index} class="story">
              <h4 class="story-title">{story.title}</h4>
              <dl class="star">
                <div>
                  <dt>Situation</dt>
                  <dd>{story.situation}</dd>
                </div>
                <div>
                  <dt>Task</dt>
                  <dd>{story.task}</dd>
                </div>
                <div>
                  <dt>Action</dt>
                  <dd>{story.action}</dd>
                </div>
                <div>
                  <dt>Result</dt>
                  <dd>{story.result}</dd>
                </div>
              </dl>
              <p class="story-foot">
                {story.competencies.map((competency) => (
                  <span class="chip" key={competency}>
                    {competency}
                  </span>
                ))}
                <Evidence dates={story.evidence_dates} onDate={onDate} />
              </p>
            </article>
          ))}
        </div>
      );
    case 'topics':
      return (
        <ul class="compile-list">
          {result.technical_topics.map((topic, index) => (
            <li key={index} class="compile-item">
              <p class="topic-name">{topic.topic}</p>
              <p class="prose">{topic.depth_notes}</p>
              <Evidence dates={topic.evidence_dates} onDate={onDate} />
            </li>
          ))}
        </ul>
      );
    case 'threads':
      return (
        <ul class="compile-list">
          {result.open_threads.map((thread, index) => (
            <li key={index} class="compile-item">
              <p class="prose">{thread}</p>
            </li>
          ))}
        </ul>
      );
  }
}

function CompilationDialog({ compilation, onClose }: { readonly compilation: Compilation; readonly onClose: () => void }): JSX.Element {
  const wide = useMediaQuery(WIDE);
  const result = compilation.result;
  const firstTab = result === null ? 'bullets' : (TABS.find(({ tab }) => count(result, tab) > 0)?.tab ?? 'bullets');
  const [tab, setTab] = useState<Tab>(firstTab);
  const [deleting, setDeleting] = useState(false);
  const [copyError, setCopyError] = useState<AppError | null>(null);
  const running = queueState.value.snapshot?.batch.running ?? false;
  const title = `${compilation.purpose}, ${compilation.period}`;

  const showDate = (date: string): void => {
    updateView({ range: 'custom', custom: { from: date, to: date }, q: '', tags: [], projects: [], skills: [] });
    onClose();
    navigate('entries');
  };

  return (
    <Dialog label={title} onClose={onClose} class="dialog-compilation" closeOnBackdrop testId="compilation-dialog">
      <div class="dialog-panel">
        <DialogHeader title={title} onClose={onClose}>
          <button
            type="button"
            class="icon-button"
            title="Delete this compilation"
            aria-label="Delete compilation"
            data-testid="compilation-delete"
            onClick={() => {
              setDeleting(true);
            }}
          >
            <Icon name="trash" size={16} />
          </button>
        </DialogHeader>
        <p class="dialog-sub">
          <StatusMarker kind={compilationMarker(compilation.status)} label={COMPILATION_STATUS_LABELS[compilation.status]} decorative />
          <span>{COMPILATION_STATUS_LABELS[compilation.status]}</span>
          <span class="mono muted">{formatDateTime(compilation.created_at)}</span>
          <span class="muted">{plural(compilation.entry_ids.length, 'entry', 'entries')}</span>
          {compilation.model !== null && (
            <span class="mono muted" title={compilation.prompt_sha256 === null ? 'Model' : `Model; prompt SHA-256 ${compilation.prompt_sha256}`}>
              {compilation.model}
            </span>
          )}
        </p>
        {result !== null && (
          <div class="tabs" role="tablist" aria-label="Sections">
            {TABS.map((item) => (
              <button
                type="button"
                role="tab"
                key={item.tab}
                aria-selected={tab === item.tab}
                class={tab === item.tab ? 'tab is-on' : 'tab'}
                data-testid={`compile-tab-${item.tab}`}
                onClick={() => {
                  setTab(item.tab);
                }}
              >
                <span>{wide ? item.label : item.short}</span>
                <span class="tab-count mono">{count(result, item.tab)}</span>
              </button>
            ))}
          </div>
        )}
        <div class="dialog-body compilation-body">
          {copyError !== null && (
            <ErrorNotice
              error={copyError}
              compact
              onDismiss={() => {
                setCopyError(null);
              }}
            />
          )}
          {compilation.status === 'failed' && compilation.last_error !== null && (
            <ErrorNotice
              error={
                new AppError({
                  code: 'COMPILATION_FAILED',
                  message: `The compilation failed: ${compilation.last_error}`,
                  hint: 'Check the tasks.compile model route and the service log, then compile the entries again.',
                  details: [{ label: 'Compilation', value: compilation.id }],
                })
              }
            />
          )}
          {(compilation.status === 'queued' || compilation.status === 'running') && (
            <div class="inline-note">
              <StatusMarker kind={compilationMarker(compilation.status)} label={COMPILATION_STATUS_LABELS[compilation.status]} decorative />
              <span>
                {compilation.status === 'running' ? 'Compiling now; this view updates when it finishes.' : 'Queued; it runs with the next batch.'}
              </span>
              {compilation.status === 'queued' && (
                <button
                  type="button"
                  class="button button-small"
                  disabled={running || batchRequest.value.pending}
                  onClick={() => void runBatchNow()}
                >
                  {running ? 'Batch running' : 'Run batch now'}
                </button>
              )}
            </div>
          )}
          {result !== null &&
            (count(result, tab) === 0 ? (
              <p class="empty-note">The entries support no {TABS.find((t) => t.tab === tab)?.label.toLowerCase()}.</p>
            ) : (
              <div role="tabpanel" data-testid="compile-panel">
                <div class="panel-tools">
                  <button
                    type="button"
                    class="button button-small"
                    onClick={() => {
                      void copy(sectionText(result, tab), TABS.find((t) => t.tab === tab)?.label.toLowerCase() ?? 'section').then(setCopyError);
                    }}
                  >
                    <Icon name="copy" size={14} />
                    <span>Copy section</span>
                  </button>
                </div>
                <ResultTab result={result} tab={tab} onDate={showDate} />
              </div>
            ))}
        </div>
        {result !== null && (
          <footer class="dialog-foot">
            <button
              type="button"
              class="button button-primary"
              onClick={() => {
                void copy(allText(compilation, result), 'the whole compilation').then(setCopyError);
              }}
              data-testid="compilation-copy-all"
            >
              <Icon name="copy" size={16} />
              <span>Copy all</span>
            </button>
          </footer>
        )}
      </div>
      {deleting && (
        <ConfirmDialog
          title="Delete compilation?"
          action="Delete compilation"
          destructive
          onCancel={() => {
            setDeleting(false);
          }}
          onConfirm={async () => {
            await api().deleteCompilation(compilation.id);
            toast('Compilation deleted');
            setDeleting(false);
            onClose();
            bumpData();
          }}
        >
          <p>
            {title} ({COMPILATION_STATUS_LABELS[compilation.status].toLowerCase()}, <span class="mono">{formatDateTime(compilation.created_at)}</span>).
          </p>
          <p>The entries stay; only this compiled material is removed. It cannot be undone.</p>
        </ConfirmDialog>
      )}
    </Dialog>
  );
}

function summaryOf(compilation: Compilation): string {
  const result = compilation.result;
  if (result === null) return compilation.status === 'failed' ? 'Failed; open for the error' : COMPILATION_STATUS_LABELS[compilation.status];
  return [
    plural(result.resume_bullets.length, 'bullet'),
    plural(result.star_stories.length, 'story', 'stories'),
    plural(result.open_threads.length, 'open thread'),
  ].join(', ');
}

export function CompilationsPage(): JSX.Element {
  const version = dataVersion.value;
  const [items, setItems] = useState<readonly Compilation[] | null>(null);
  const [error, setError] = useState<AppError | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    api()
      .listCompilations(controller.signal)
      .then((list) => {
        setItems(list);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (isAbort(caught)) return;
        setError(toAppError(caught, 'Load compilations'));
      });
    return () => {
      controller.abort();
    };
  }, [version, reload]);

  const open = items?.find((item) => item.id === openId) ?? null;
  const chooseEntries = (): void => {
    navigate('entries');
    toast('Select entries, then choose Compile');
  };

  return (
    <main class="page page-compilations" data-testid="compilations-page">
      <div class="toolbar">
        <div class="toolbar-row">
          <p class="toolbar-note">Resume bullets, STAR stories and open threads compiled from entries you select.</p>
          <span class="toolbar-spacer" />
          <button type="button" class="button button-primary" onClick={chooseEntries} title="Go to Entries and select the entries to compile">
            <Icon name="compile" size={16} />
            <span>Compile entries</span>
          </button>
        </div>
      </div>
      {error !== null && (
        <div class="list-error">
          <ErrorNotice
            error={error}
            onRetry={() => {
              setReload((n) => n + 1);
            }}
          />
        </div>
      )}
      <div class="list-head">
        <span class="list-head-text">{items === null ? 'Loading compilations' : <span class="mono">{plural(items.length, 'compilation')}</span>}</span>
      </div>
      <div class="list-scroll" ref={scroller}>
        {items !== null && items.length > 0 && (
          <div class="compile-table" role="table" aria-label="Compilations">
            <div class="compile-row compile-header" role="row">
              <span role="columnheader" title="Status: ring queued, half ring running, disc done, cross failed">
                <span class="visually-hidden">Status</span>
              </span>
              <span role="columnheader">Created</span>
              <span role="columnheader">Purpose</span>
              <span role="columnheader">Period</span>
              <span role="columnheader" title="Entries compiled">
                Entries
              </span>
              <span role="columnheader">Result</span>
            </div>
            {items.map((item) => (
              <div
                key={item.id}
                class="compile-row"
                role="row"
                tabIndex={0}
                data-testid="compilation-row"
                data-status={item.status}
                onClick={() => {
                  setOpenId(item.id);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') setOpenId(item.id);
                }}
              >
                <span role="cell">
                  <StatusMarker kind={compilationMarker(item.status)} label={COMPILATION_STATUS_LABELS[item.status]} />
                </span>
                <span role="cell" class="mono cell-created">
                  {formatDateTime(item.created_at)}
                </span>
                <span role="cell" class="cell-purpose">
                  {item.purpose}
                </span>
                <span role="cell" class="cell-period">
                  {item.period}
                </span>
                <span role="cell" class="mono cell-count">
                  {item.entry_ids.length}
                </span>
                <span role="cell" class="muted cell-result">
                  {summaryOf(item)}
                </span>
              </div>
            ))}
          </div>
        )}
        {items !== null && items.length === 0 && (
          <div class="empty-state">
            <p class="empty-text">Compilations you start from a selection of entries appear here.</p>
            <div class="empty-action">
              <button type="button" class="button button-primary" onClick={chooseEntries}>
                Choose entries
              </button>
            </div>
          </div>
        )}
      </div>
      {open !== null && (
        <CompilationDialog
          compilation={open}
          onClose={() => {
            setOpenId(null);
          }}
        />
      )}
    </main>
  );
}
