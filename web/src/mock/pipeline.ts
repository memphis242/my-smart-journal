import type { QueueItem, QueueSnapshot } from '../api/types';
import { assert } from '../lib/assert';
import type { Tasks } from './http';
import { isActive, type Store, type StoredEntry, type StoredJob } from './state';

const TRANSCRIPTS = [
  'I reviewed the Atlas deployment today. The team agreed to add monitoring before the next release. Next week I plan to check the dashboard.',
  'Today I paired with a colleague on the data pipeline. We found a missing validation step and added a clear error message. The review is still pending.',
  'I spent the morning preparing the project demo. The new workflow is easier to follow. I plan to collect feedback from the team.',
] as const;

/** The simulated worker wakes every tick; a heartbeat older than max(10 s, 3 ticks) is stale. */
const TICK_MS = 2000;
const BATCH_INTERVAL_MS = 86_400_000;

const SEEDED_BATCH_ERROR = {
  code: 'BATCH_FAILED',
  detail: 'The batch stopped after an unexpected error in the extract wave; 4 jobs were left queued.',
} as const;

export class Pipeline {
  readonly #store: Store;
  readonly #tasks: Tasks;
  readonly #now: () => number;
  readonly #delay: number;
  readonly #pickupDelay: number;
  readonly #stale: boolean;
  /** The last heartbeat of a worker that stopped; only used while stale. */
  readonly #staleSeen: number;
  #nextBatch: number;
  #pending = false;
  #work: Promise<void> = Promise.resolve();
  readonly #batch: {
    running: boolean;
    started_at: string | null;
    trigger: 'manual' | 'schedule' | null;
    last_finished_at: string | null;
    last_result: { readonly processed: number; readonly failed: number } | null;
    last_error: { readonly code: string; readonly detail: string } | null;
  };

  constructor(
    store: Store,
    tasks: Tasks,
    now: () => number,
    delay: number,
    stale: boolean,
    pickupDelay = 0,
    batchError = false,
  ) {
    this.#store = store;
    this.#tasks = tasks;
    this.#now = now;
    this.#delay = delay;
    this.#pickupDelay = pickupDelay;
    this.#stale = stale;
    this.#staleSeen = now() - 3 * 3600000;
    // The seeded scheduled batch finished 47 minutes ago; the next one is a day after it started.
    this.#nextBatch = now() - 47 * 60000 + BATCH_INTERVAL_MS;
    this.#batch = {
      running: false,
      started_at: null,
      trigger: null,
      last_finished_at: new Date(now() - 47 * 60000).toISOString(),
      last_result: batchError ? { processed: 5, failed: 0 } : { processed: 9, failed: 0 },
      last_error: batchError ? SEEDED_BATCH_ERROR : null,
    };
  }

  get running(): boolean {
    return this.#batch.running;
  }

  get pending(): boolean {
    return this.#pending;
  }

  get stale(): boolean {
    return this.#stale;
  }

  /** Records a manual request; repeated requests while one is pending are idempotent. */
  request(): void {
    assert(!this.running, 'the router checks that no batch is running');
    if (this.#pending) return;
    this.#pending = true;
    // A stopped worker never picks the request up; it stays pending, as on the server.
    if (this.#stale) return;

    const pickup = async (): Promise<void> => {
      await this.#tasks.delay(this.#pickupDelay);
      if (!this.#pending) return;
      this.#pending = false;
      if (this.#stale || this.running) return;
      this.#start('manual');
    };
    this.#tasks.track(pickup());
  }

  schedule(): void {
    if (
      !this.#stale
      && !this.#pending
      && !this.running
      && this.#now() >= this.#nextBatch
    ) {
      this.#nextBatch += BATCH_INTERVAL_MS;
      this.#start('schedule');
    }
  }

  #start(trigger: 'manual' | 'schedule'): void {
    assert(!this.running, 'a batch starts only after the single-flight check');
    assert(!this.#pending || trigger === 'manual', 'a pending request starts a manual batch');

    this.#batch.running = true;
    this.#batch.started_at = this.#store.timestamp();
    this.#batch.trigger = trigger;
    const revisions = [...this.#store.entries.values()].map((entry) => ({ entry, revision: entry.revision }));
    const compilations = [...this.#store.compilations.values()].filter((item) => item.status === 'queued');

    this.#work = (async () => {
      let processed = 0;
      let failed = 0;
      let failure: { readonly code: string; readonly detail: string } | null = null;
      const attempted = new Set<string>();

      try {
        // Yield once so callers can observe the running batch before jobs finish.
        await this.#tasks.delay(0);
        for (;;) {
          const ready = revisions.flatMap(({ entry, revision }) => {
            if (!this.#store.entries.has(entry.id) || entry.revision !== revision) return [];
            return this.#store.currentJobs(entry)
              .filter((job) => job.status === 'queued'
                && job.not_before <= this.#now() / 1000
                && !attempted.has(job.id)
                && job.dependencies.every((id) => entry.jobs.some((parent) => parent.id === id && parent.status === 'done')))
              .map((job) => ({ entry, job }));
          }).sort((left, right) => left.job.created_at.localeCompare(right.job.created_at)
            || left.job.id.localeCompare(right.job.id));
          const item = ready[0];
          if (item === undefined) break;

          const { entry, job } = item;
          void attempted.add(job.id); // Each job is attempted at most once in this batch.
          job.status = 'running';
          job.attempts += 1;
          await this.#tasks.delay(this.#delay);
          if (!this.#store.entries.has(entry.id) || entry.revision !== job.revision) continue;
          this.finish(entry, job);
          job.status = 'done';
          job.last_error = null;
          job.finished_at = this.#store.timestamp();
          processed += 1;

        }

        for (const compilation of compilations) {
          if (!this.#store.compilations.has(compilation.id)) continue;
          compilation.status = 'running';
          await this.#tasks.delay(this.#delay);
          if (!this.#store.compilations.has(compilation.id)) continue;
          this.#store.finishCompilation(compilation);
          processed += 1;
        }
      } catch (error) {
        failed = 1;
        failure = {
          code: 'BATCH_FAILED',
          detail: error instanceof Error
            ? `The mock batch failed: ${error.message}`
            : 'The mock batch failed for an unknown reason. Inspect the mock worker and retry the batch.',
        };
        throw error;
      } finally {
        this.#batch.running = false;
        this.#batch.last_finished_at = this.#store.timestamp();
        this.#batch.last_result = { processed, failed };
        this.#batch.last_error = failure;
      }
    })();

    this.#tasks.track(this.#work);
  }

  finish(entry: StoredEntry, job: StoredJob): void {
    const text = this.#store.summary(entry).text;
    switch (job.kind) {
      case 'transcribe': {
        const index = parseInt(entry.id.slice(-6), 16) % TRANSCRIPTS.length;
        const transcript = TRANSCRIPTS[index];
        assert(transcript !== undefined, 'transcript index is within the canned set');
        entry.raw = transcript;
        job.result_artifact_id = this.#store.artifact(entry, 'raw_transcript', transcript).id;
        break;
      }
      case 'cleanup': {
        const cleaned = text.replace(/\b(?:um|uh)\b[,\s]*/gi, '')
          .replace(/[ \t]+/g, ' ')
          .trim()
          .replace(/(^|[.!?]\s+)([a-z])/g, (_match, prefix: string, letter: string) => prefix + letter.toUpperCase());
        job.result_artifact_id = this.#store.artifact(entry, 'cleaned', cleaned).id;
        break;
      }
      case 'summarize':
        job.result_artifact_id = this.#store.artifact(
          entry,
          'summary',
          (/^[\s\S]*?[.!?](?:\s|$)/.exec(text)?.[0].trim() ?? text).slice(0, 160),
        ).id;
        break;
      case 'extract':
        job.result_artifact_id = this.#store.artifact(entry, 'extraction', JSON.stringify({
          projects: this.#store.knownProjects.filter((value) => text.toLowerCase().includes(value.toLowerCase())),
          skills_technologies: this.#store.knownSkills.filter((value) => text.toLowerCase().includes(value.toLowerCase())),
          accomplishments: [],
          people_roles: [],
          star_fragments: [],
          tags: entry.tags,
        })).id;
        break;
      case 'review':
        assert(job.target === 'cleanup' || job.target === 'summarize' || job.target === 'extract', 'review job has a producer target');
        this.#store.accept(entry, job.target);
        break;
      case 'embed':
        break;
      default:
        assert(false, `pipeline job kind ${job.kind} is supported`);
    }
  }

  snapshot(): QueueSnapshot {
    const jobs: { readonly created_at: string; readonly item: QueueItem }[] = [...this.#store.entries.values()]
      .flatMap((entry) => this.#store.currentJobs(entry)
        .filter((job) => isActive(job.status))
        .map((job) => {
          assert(isActive(job.status), 'queue contains only nonterminal and failed jobs');
          return {
            created_at: job.created_at,
            item: {
              entry_id: entry.id,
              entry_title: entry.title,
              entry_date: entry.entry_date,
              job_id: job.id,
              kind: job.kind,
              target: job.target,
              turn: job.turn,
              status: job.status,
              blocked_by: this.#store.blocked(entry, job),
              last_error: job.last_error,
              not_before: new Date(job.not_before * 1000).toISOString(),
            } satisfies QueueItem,
          };
        }));

    for (const compilation of this.#store.compilations.values()) {
      if (compilation.status === 'queued' || compilation.status === 'running' || compilation.status === 'failed') {
        jobs.push({
          created_at: compilation.created_at,
          item: {
            entry_id: null,
            entry_title: null,
            entry_date: null,
            job_id: compilation.job_id,
            kind: 'compile',
            target: null,
            turn: null,
            status: compilation.status,
            blocked_by: [],
            last_error: compilation.last_error,
            not_before: null,
          },
        });
      }
    }

    jobs.sort((left, right) => left.created_at.localeCompare(right.created_at)
      || left.item.job_id.localeCompare(right.item.job_id));
    const counts = { queued: 0, running: 0, failed: 0, blocked: 0 };
    for (const { item } of jobs) {
      if (item.status === 'queued' && item.blocked_by.length > 0) counts.blocked += 1;
      else counts[item.status] += 1;
    }

    const now = this.#now();
    const lastSeen = this.#stale ? this.#staleSeen : now - (now % TICK_MS);
    return {
      batch: { ...this.#batch, request_pending: this.#pending },
      worker: {
        last_seen_at: new Date(lastSeen).toISOString(),
        interval_s: BATCH_INTERVAL_MS / 1000,
        // The next wake: due now while a request waits, otherwise one tick after the heartbeat.
        next_run_at: this.#stale ? null : new Date(this.#pending ? lastSeen : lastSeen + TICK_MS).toISOString(),
        next_batch_at: this.#stale ? null : new Date(this.#nextBatch).toISOString(),
        stale: this.#stale,
      },
      counts,
      items: jobs.slice(0, 200).map(({ item }) => item),
    };
  }
}
