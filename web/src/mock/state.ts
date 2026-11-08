import type { Artifact, BlockedBy, Compilation, CompileResult, EntryDetail, EntrySummary, Extraction, Job, JobStatus, ReviewSession, ReviewTarget } from '../api/types';
import { assert } from '../lib/assert';
import { HttpProblem, localDate } from './http';
import type { SeedCompilation, SeedEntry } from './seed-types';

type Mutable<T> = { -readonly [K in keyof T]: T[K] };
export interface StoredArtifact extends Artifact {
  readonly entry_id: string;
  readonly prompt_sha256: string | null;
  readonly superseded_by: null;
  readonly reviewed_artifact_id: string | null;
}
export interface StoredJob extends Mutable<Omit<Job, 'blocked_by'>> {
  readonly entry_id: string;
  readonly dependencies: readonly string[];
  not_before: number;
  input_artifact_id: string | null;
  review_artifact_id: string | null;
  result_artifact_id: string | null;
}
export interface JobView extends Job {
  readonly entry_id: string;
  readonly not_before: number;
  readonly input_artifact_id: string | null;
  readonly review_artifact_id: string | null;
  readonly result_artifact_id: string | null;
  readonly lease_until: null;
  readonly claim_token: null;
  readonly claim_protocol: number;
}
interface StoredSession extends Mutable<ReviewSession> {
  final_artifact: StoredArtifact | null;
  readonly history: readonly StoredArtifact[];
}
export interface StoredEntry {
  readonly id: string;
  readonly created_at: string;
  entry_date: string;
  title: string | null;
  tags: readonly string[];
  readonly source: 'text' | 'audio';
  revision: number;
  /** Optimistic-concurrency version; every PATCH increments it. */
  version: number;
  raw: string;
  readonly artifacts: StoredArtifact[];
  readonly jobs: StoredJob[];
  sessions: Record<ReviewTarget, StoredSession>;
}
export interface StoredCompilation extends Mutable<Compilation> {
  readonly job_id: string;
}
const HASH = '0123456789abcdef'.repeat(4);
const MODELS: Readonly<Record<string, string>> = {
  cleaned: 'phi-4-mini-instruct', summary: 'gemma-4-26b-a4b', extraction: 'gemma-4-26b-a4b',
  review: 'gemma-4-26b-a4b', raw_transcript: 'faster-whisper-large-v3',
};
export const emptyExtraction = (): Extraction => ({ projects: [], skills_technologies: [], accomplishments: [], people_roles: [], star_fragments: [], tags: [] });
function sessions(): Record<ReviewTarget, StoredSession> {
  const pending = (): StoredSession => ({ status: 'pending', turns_used: 0, remaining_claims: [], final_artifact: null, history: [] });
  return { cleanup: pending(), summarize: pending(), extract: pending() };
}
export function compareEntries(left: EntrySummary, right: EntrySummary): number {
  return left.entry_date.localeCompare(right.entry_date) || left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id);
}

/** Owns mutable records. HTTP views contain detached arrays and objects. */
export class Store {
  readonly entries = new Map<string, StoredEntry>();
  readonly compilations = new Map<string, StoredCompilation>();
  readonly knownProjects: readonly string[];
  readonly knownSkills: readonly string[];
  readonly #now: () => number;
  #sequence = 0;

  constructor(seeds: readonly SeedEntry[], compilations: readonly SeedCompilation[], now: () => number) {
    this.#now = now;
    this.knownProjects = [...new Set(seeds.flatMap((seed) => seed.projects))];
    this.knownSkills = [...new Set(seeds.flatMap((seed) => seed.skills))];
    const records = seeds.map((seed) => {
      const date = new Date(now());
      date.setDate(date.getDate() - seed.days_ago);
      const [hours, minutes] = seed.time.split(':').map(Number);
      date.setHours(hours ?? 12, minutes ?? 0, 0, 0);
      const entry = this.create(seed.source, seed.raw, seed.title, seed.tags, localDate(date.getTime()), date.toISOString());
      if (seed.source === 'audio') this.artifact(entry, 'raw_transcript', seed.raw, entry.created_at);
      this.artifact(entry, 'cleaned', seed.cleaned, entry.created_at);
      this.artifact(entry, 'summary', seed.summary, entry.created_at);
      this.artifact(entry, 'extraction', JSON.stringify({ projects: seed.projects, skills_technologies: seed.skills, accomplishments: seed.accomplishments,
        people_roles: seed.people_roles, star_fragments: seed.star_fragments, tags: seed.tags } satisfies Extraction), entry.created_at);
      for (const job of entry.jobs) { job.status = 'done'; job.attempts = 1; job.finished_at = entry.created_at; }
      for (const target of ['cleanup', 'summarize', 'extract'] as const) this.accept(entry, target);
      return entry;
    });
    const newest = seeds.map((seed, index) => ({ seed, index })).sort((a, b) => a.seed.days_ago - b.seed.days_ago || a.index - b.index).slice(0, 2);
    const exceptional = new Set(newest.map(({ index }) => index));
    for (const { index } of newest) {
      const entry = records[index];
      assert(entry !== undefined, 'seed record exists');
      this.resetDerived(entry, entry.source === 'audio');
    }
    const failedIndex = seeds.findIndex((seed, index) => !exceptional.has(index) && seed.source === 'audio' && seed.days_ago >= 3 && seed.days_ago <= 30);
    const failed = records[failedIndex];
    if (failed !== undefined) {
      void exceptional.add(failedIndex); // The set only tracks which seed rules have already been used.
      this.resetDerived(failed, true);
      const job = failed.jobs.find((item) => item.kind === 'transcribe');
      assert(job !== undefined, 'audio pipeline has a transcription job');
      job.status = 'failed'; job.attempts = 3; job.finished_at = failed.created_at;
      job.last_error = 'RunPod endpoint whisper-large-v3 returned HTTP 413 Payload Too Large: the encoded /run body exceeds runpod_max_payload_bytes (9500000). Record in Opus/compressed format or split the recording, then retry.';
    }
    const attentionIndex = seeds.findIndex((seed, index) => !exceptional.has(index) && seed.source === 'text' && seed.days_ago >= 4 && seed.days_ago <= 40 && seed.accomplishments.length > 0);
    const attention = records[attentionIndex];
    if (attention !== undefined) {
      void exceptional.add(attentionIndex); // Prevent different seed states from hiding each other.
      attention.sessions.extract.status = 'needs_attention'; attention.sessions.extract.turns_used = 3;
      attention.sessions.extract.remaining_claims = ['The extraction lists a 40% latency reduction; the entry reports 18 ms to 21 ms, an increase.'];
      const index = attention.artifacts.findIndex((artifact) => artifact.kind === 'extraction');
      const candidate = attention.artifacts[index];
      assert(candidate !== undefined, 'processed seed has an extraction');
      const marked = { ...candidate, needs_attention: true };
      attention.artifacts[index] = marked;
      attention.sessions.extract.final_artifact = marked;
    }
    const backoffIndex = seeds.findIndex((seed, index) => !exceptional.has(index) && seed.days_ago >= 1 && seed.days_ago <= 10);
    const backoff = records[backoffIndex];
    if (backoff !== undefined) {
      const summarize = backoff.jobs.find((job) => job.kind === 'summarize');
      assert(summarize !== undefined, 'pipeline has a summary job');
      const affected = new Set([summarize.id, ...backoff.jobs.filter((job) => job.kind === 'embed' || (job.kind === 'review' && job.target === 'summarize')).map((job) => job.id)]);
      for (const job of backoff.jobs) if (affected.has(job.id)) { job.status = 'queued'; job.attempts = 0; job.finished_at = null; }
      summarize.attempts = 2; summarize.not_before = now() / 1000 + 360;
      summarize.last_error = 'OpenAI-compatible endpoint gemma-26b returned HTTP 503 Service Unavailable; retry 2 of 5 scheduled.';
      this.removeArtifacts(backoff, new Set(['summary']));
      backoff.sessions.summarize = sessions().summarize;
    }
    const dates = (indexes: readonly number[]): readonly string[] => indexes.flatMap((index) => records[index]?.entry_date ?? []);
    for (const seed of compilations) {
      const entryIds = seed.entries.flatMap((index) => records[index]?.id ?? []);
      const result: CompileResult = {
        resume_bullets: seed.resume_bullets.map(({ evidence, ...bullet }) => ({ ...bullet, evidence_dates: dates(evidence) })),
        star_stories: seed.star_stories.map(({ evidence, ...story }) => ({ ...story, evidence_dates: dates(evidence) })),
        technical_topics: seed.technical_topics.map(({ evidence, ...topic }) => ({ ...topic, evidence_dates: dates(evidence) })),
        open_threads: [...seed.open_threads],
      };
      const compilation = this.compile(entryIds, seed.purpose, seed.period);
      compilation.created_at = new Date(now() - seed.days_ago * 86400000).toISOString();
      compilation.status = 'done'; compilation.result = result; compilation.model = MODELS['summary'] ?? null; compilation.prompt_sha256 = HASH;
    }
  }

  id(): string { this.#sequence += 1; return this.#sequence.toString(16).padStart(32, '0'); }
  timestamp(): string { return new Date(this.#now()).toISOString(); }

  create(source: 'text' | 'audio', raw: string, title: string | null, tags: readonly string[], date: string, createdAt = this.timestamp(), audioExtension = 'webm'): StoredEntry {
    const entry: StoredEntry = { id: this.id(), created_at: createdAt, entry_date: date, title, tags: [...tags], source, revision: 1, version: 1, raw,
      artifacts: [], jobs: [], sessions: sessions() };
    this.artifact(entry, source === 'audio' ? 'raw_audio' : 'user_text', source === 'audio' ? `audio/${entry.id}/recording.${audioExtension}` : raw, createdAt);
    this.pipeline(entry, source === 'audio', createdAt);
    void this.entries.set(entry.id, entry); // Store insertion is the operation; no previous value is needed.
    return entry;
  }

  resetDerived(entry: StoredEntry, audio: boolean): void {
    this.removeArtifacts(entry, new Set(['cleaned', 'summary', 'extraction', 'review', 'raw_transcript']));
    entry.jobs.splice(0, entry.jobs.length);
    entry.sessions = sessions();
    this.pipeline(entry, audio, entry.created_at);
  }

  removeArtifacts(entry: StoredEntry, kinds: ReadonlySet<string>): void {
    const retained = entry.artifacts.filter((artifact) => !kinds.has(artifact.kind));
    entry.artifacts.splice(0, entry.artifacts.length, ...retained);
  }

  pipeline(entry: StoredEntry, audio: boolean, createdAt = this.timestamp()): void {
    const add = (kind: string, dependencies: readonly string[], target: ReviewTarget | null = null, turn = 0): StoredJob => {
      const job: StoredJob = { id: this.id(), entry_id: entry.id, kind, target, turn, dependencies, status: 'queued', attempts: 0, last_error: null,
        not_before: this.#now() / 1000, created_at: createdAt, finished_at: null, revision: entry.revision,
        input_artifact_id: null, review_artifact_id: null, result_artifact_id: null };
      entry.jobs.push(job);
      return job;
    };
    const transcript = audio ? add('transcribe', []) : null;
    const cleanup = add('cleanup', transcript === null ? [] : [transcript.id], 'cleanup');
    const reviewCleanup = add('review', [cleanup.id], 'cleanup', 1);
    const summarize = add('summarize', [reviewCleanup.id], 'summarize');
    const extract = add('extract', [reviewCleanup.id], 'extract');
    const reviewSummary = add('review', [summarize.id], 'summarize', 1);
    const reviewExtract = add('review', [extract.id], 'extract', 1);
    void add('embed', [reviewCleanup.id, reviewSummary.id, reviewExtract.id]); // Embedding has no downstream dependency.
  }

  revise(entry: StoredEntry, text: string): void {
    for (const job of entry.jobs) if (job.status === 'queued' || job.status === 'running' || job.status === 'failed') {
      job.status = 'superseded'; job.last_error = 'Entry text revised; use current jobs'; job.finished_at = this.timestamp();
    }
    entry.revision += 1; entry.raw = text; entry.sessions = sessions();
    this.artifact(entry, 'user_text', text);
    this.pipeline(entry, false);
  }

  artifact(entry: StoredEntry, kind: string, content: string, createdAt = this.timestamp()): StoredArtifact {
    const target = ({ cleaned: 'cleanup', summary: 'summarize', extraction: 'extract' } as Readonly<Record<string, ReviewTarget>>)[kind] ?? null;
    const artifact: StoredArtifact = { id: this.id(), entry_id: entry.id, kind, content, model: MODELS[kind] ?? null, prompt_sha256: MODELS[kind] === undefined ? null : HASH,
      created_at: createdAt, revision: entry.revision, needs_attention: false, target, turn: 0, superseded_by: null, reviewed_artifact_id: null };
    entry.artifacts.push(artifact);
    if (target !== null) entry.sessions[target].final_artifact = artifact;
    return artifact;
  }

  accept(entry: StoredEntry, target: ReviewTarget): void {
    const kind = { cleanup: 'cleaned', summarize: 'summary', extract: 'extraction' }[target];
    const candidate = this.currentArtifacts(entry).findLast((artifact) => artifact.kind === kind);
    assert(candidate !== undefined, `completed ${target} has a candidate artifact`);
    entry.sessions[target] = { status: 'accepted', turns_used: 1, remaining_claims: [], final_artifact: candidate, history: [] };
  }

  currentArtifacts(entry: StoredEntry): readonly StoredArtifact[] { return entry.artifacts.filter((artifact) => artifact.revision === entry.revision); }
  currentJobs(entry: StoredEntry): readonly StoredJob[] { return entry.jobs.filter((job) => job.revision === entry.revision); }
  blocked(entry: StoredEntry, job: StoredJob): readonly BlockedBy[] {
    const ancestors = new Map<string, StoredJob>();
    const visit = (id: string): void => {
      if (ancestors.has(id)) return;
      const parent = entry.jobs.find((candidate) => candidate.id === id);
      assert(parent !== undefined, 'pipeline dependency exists');
      void ancestors.set(id, parent); // Track visited ancestors to avoid duplicate blockers.
      for (const dependency of parent.dependencies) visit(dependency);
    };
    for (const dependency of job.dependencies) visit(dependency);
    return [...ancestors.values()].filter((parent) => parent.status === 'failed').sort((a, b) => a.id.localeCompare(b.id))
      .map((parent) => ({ job_id: parent.id, kind: parent.kind, message: `blocked by failed ${parent.kind} job ${parent.id}` }));
  }
  jobView(entry: StoredEntry, job: StoredJob): JobView {
    return { id: job.id, entry_id: job.entry_id, kind: job.kind, status: job.status, attempts: job.attempts, last_error: job.last_error,
      created_at: job.created_at, finished_at: job.finished_at, revision: job.revision, target: job.target, turn: job.turn,
      not_before: job.not_before, blocked_by: this.blocked(entry, job), input_artifact_id: job.input_artifact_id,
      review_artifact_id: job.review_artifact_id, result_artifact_id: job.result_artifact_id,
      lease_until: null, claim_token: null, claim_protocol: job.status === 'running' ? 4 : 0 };
  }
  extraction(entry: StoredEntry): Extraction {
    const artifact = this.currentArtifacts(entry).findLast((item) => item.kind === 'extraction');
    // Extraction artifacts are constructed exclusively by this store from typed values.
    return artifact === undefined ? emptyExtraction() : JSON.parse(artifact.content) as Extraction;
  }
  summary(entry: StoredEntry): EntrySummary {
    const jobs = this.currentJobs(entry);
    const artifacts = this.currentArtifacts(entry);
    const extraction = this.extraction(entry);
    const needsAttention = Object.values(entry.sessions).some((session) => session.status === 'needs_attention');
    const statuses = new Set(jobs.map((job) => job.status));
    const status = statuses.has('failed') ? 'failed' : statuses.has('running') ? 'processing' : statuses.has('queued') ? 'queued' : needsAttention ? 'needs_attention' : 'done';
    return { id: entry.id, entry_date: entry.entry_date, created_at: entry.created_at, title: entry.title, tags: [...entry.tags], source: entry.source,
      summary: artifacts.findLast((item) => item.kind === 'summary')?.content ?? null,
      text: artifacts.findLast((item) => item.kind === 'cleaned')?.content ?? artifacts.findLast((item) => item.kind === 'user_text' || item.kind === 'raw_transcript')?.content ?? '',
      projects: [...extraction.projects], skills: [...extraction.skills_technologies], status, revision: entry.revision, version: entry.version };
  }
  detail(entry: StoredEntry, history = false): EntryDetail {
    return {
      ...this.summary(entry),
      needs_attention: Object.values(entry.sessions).some((session) => session.status === 'needs_attention'),
      compilation_count: [...this.compilations.values()]
        .filter((compilation) => compilation.entry_ids.includes(entry.id)).length,
      artifacts: (history ? entry.artifacts : this.currentArtifacts(entry)).map((artifact) => ({ ...artifact })),
      jobs: (history ? entry.jobs : this.currentJobs(entry)).map((job) => this.jobView(entry, job)),
      review_sessions: structuredClone(entry.sessions),
    };
  }

  deleteEntry(id: string): void {
    for (const compilation of this.compilations.values()) {
      if (compilation.entry_ids.includes(id)) {
        // Cascading deletion removes the complete citing compilation, including its queued work.
        void this.compilations.delete(compilation.id);
      }
    }
    // Entry existence is validated by the router before deletion.
    void this.entries.delete(id);
  }

  entry(id: string): StoredEntry {
    const entry = this.entries.get(id);
    if (entry === undefined) throw new HttpProblem(404, 'Not Found', `Entry not found: ${id}`, 'ENTRY_NOT_FOUND');
    return entry;
  }
  compile(entryIds: readonly string[], purpose: string, period: string): StoredCompilation {
    const compilation: StoredCompilation = { id: this.id(), job_id: this.id(), created_at: this.timestamp(), status: 'queued', entry_ids: [...entryIds], purpose, period,
      result: null, last_error: null, model: null, prompt_sha256: null };
    void this.compilations.set(compilation.id, compilation); // The stored record is returned explicitly.
    return compilation;
  }
  finishCompilation(compilation: StoredCompilation): void {
    const entries = compilation.entry_ids.flatMap((id) => this.entries.get(id) ?? []);
    compilation.result = {
      resume_bullets: entries.flatMap((entry) => this.extraction(entry).accomplishments.map((item) => ({ text: `${item.description}${item.metrics.length === 0 ? '' : ` (${item.metrics.join('; ')})`}`, evidence_dates: [entry.entry_date] }))),
      star_stories: entries.flatMap((entry) => this.extraction(entry).star_fragments.map((fragment) => ({ ...fragment, title: entry.title ?? entry.entry_date,
        evidence_dates: [entry.entry_date], competencies: this.extraction(entry).skills_technologies }))),
      technical_topics: entries.flatMap((entry) => this.extraction(entry).skills_technologies.map((topic) => ({ topic, depth_notes: `Applied ${topic} in ${entry.title ?? 'journal work'}.`, evidence_dates: [entry.entry_date] }))),
      open_threads: entries.flatMap((entry) => this.summary(entry).text.split(/(?<=[.!?])\s+|\n+/).filter((line) => /\b(not yet|still|pending|waiting|plan|next week|TODO)\b/i.test(line))),
    };
    compilation.status = 'done'; compilation.model = MODELS['summary'] ?? null; compilation.prompt_sha256 = HASH;
  }
}

export function isActive(status: JobStatus): status is 'queued' | 'running' | 'failed' { return status === 'queued' || status === 'running' || status === 'failed'; }
