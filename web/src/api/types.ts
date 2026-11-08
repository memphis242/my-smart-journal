/**
 * Response and request shapes of the journal API v1 (docs/api-v1-contract.md).
 *
 * Every response is checked against these shapes at the boundary by the guards in
 * `guards.ts`; the rest of the app trusts them.
 */

export const ENTRY_STATUSES = ['queued', 'processing', 'done', 'needs_attention', 'failed'] as const;
export type EntryStatus = (typeof ENTRY_STATUSES)[number];

export const ENTRY_SOURCES = ['text', 'audio'] as const;
export type EntrySource = (typeof ENTRY_SOURCES)[number];

export interface EntrySummary {
  readonly id: string;
  /** Journal date, `YYYY-MM-DD`. */
  readonly entry_date: string;
  /** ISO 8601 timestamp. */
  readonly created_at: string;
  readonly title: string | null;
  readonly tags: readonly string[];
  readonly source: EntrySource;
  readonly summary: string | null;
  /** Final cleaned text when available, else the raw text. */
  readonly text: string;
  readonly projects: readonly string[];
  readonly skills: readonly string[];
  readonly status: EntryStatus;
  readonly revision: number;
  /**
   * Optimistic-concurrency version; increments on every PATCH. Null when the
   * server does not report it (then edits are sent without expected_version).
   */
  readonly version: number | null;
}

export interface EntryPage {
  readonly items: readonly EntrySummary[];
  readonly total: number;
}

export const ARTIFACT_KINDS = [
  'raw_audio',
  'raw_transcript',
  'user_text',
  'cleaned',
  'summary',
  'extraction',
  'review',
] as const;
export type ArtifactKind = (typeof ARTIFACT_KINDS)[number];

export interface Artifact {
  readonly id: string;
  /** One of ARTIFACT_KINDS today; kept open so a new server-side kind does not reject the entry. */
  readonly kind: string;
  readonly content: string;
  readonly model: string | null;
  readonly created_at: string;
  readonly revision: number;
  readonly needs_attention: boolean;
  readonly target: string | null;
  readonly turn: number | null;
}

export const JOB_STATUSES = ['queued', 'running', 'done', 'failed', 'superseded'] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export interface BlockedBy {
  readonly job_id: string;
  readonly kind: string;
  readonly message: string;
}

export interface Job {
  readonly id: string;
  /** transcribe, cleanup, review, revise, summarize, extract, embed, ... */
  readonly kind: string;
  readonly status: JobStatus;
  readonly attempts: number;
  readonly last_error: string | null;
  readonly created_at: string;
  readonly finished_at: string | null;
  readonly revision: number;
  readonly target: string | null;
  readonly turn: number | null;
  readonly blocked_by: readonly BlockedBy[];
}

export const REVIEW_TARGETS = ['cleanup', 'summarize', 'extract'] as const;
export type ReviewTarget = (typeof REVIEW_TARGETS)[number];

export const REVIEW_SESSION_STATUSES = ['accepted', 'needs_attention', 'pending', 'disabled'] as const;
export type ReviewSessionStatus = (typeof REVIEW_SESSION_STATUSES)[number];

export interface ReviewSession {
  readonly status: ReviewSessionStatus;
  readonly turns_used: number;
  readonly remaining_claims: readonly string[];
}

export interface EntryDetail extends EntrySummary {
  readonly needs_attention: boolean;
  /** Compilations that cite this entry; deleting the entry deletes them too. */
  readonly compilation_count: number;
  readonly artifacts: readonly Artifact[];
  readonly jobs: readonly Job[];
  readonly review_sessions: Readonly<Partial<Record<ReviewTarget, ReviewSession>>>;
}

/** Parsed content of the current `extraction` artifact. */
export interface Extraction {
  readonly projects: readonly string[];
  readonly skills_technologies: readonly string[];
  readonly accomplishments: readonly { readonly description: string; readonly metrics: readonly string[] }[];
  readonly people_roles: readonly { readonly name: string; readonly role: string }[];
  readonly star_fragments: readonly {
    readonly situation: string;
    readonly task: string;
    readonly action: string;
    readonly result: string;
  }[];
  readonly tags: readonly string[];
}

export interface Neighbors {
  readonly newer: string | null;
  readonly older: string | null;
}

export interface FacetValue {
  readonly value: string;
  readonly count: number;
}

export interface Facets {
  readonly tags: readonly FacetValue[];
  readonly projects: readonly FacetValue[];
  readonly skills: readonly FacetValue[];
}

/** Filter parameters shared by entries, neighbors, search and retrieve. */
export interface EntryFilters {
  readonly from?: string;
  readonly to?: string;
  readonly tag?: readonly string[];
  readonly project?: readonly string[];
  readonly skill?: readonly string[];
}

export type SortOrder = 'asc' | 'desc';

export interface Unavailable {
  readonly code: string;
  readonly detail: string;
}

export const SEARCH_TIERS = [1, 2, 3, 4, 5] as const;
export type SearchTier = (typeof SEARCH_TIERS)[number];

export interface SearchHit {
  readonly entry_id: string;
  readonly score: number;
  readonly snippet: string | null;
  /** Tier 5 only: the best matching chunk. */
  readonly passage: string | null;
  /** Present when the server embeds the summary item in each hit. */
  readonly entry: EntrySummary | null;
}

export interface SearchTierEvent {
  readonly type: 'tier';
  readonly tier: SearchTier;
  readonly name: string;
  readonly results: readonly SearchHit[];
  readonly elapsed_ms: number;
  readonly unavailable: Unavailable | null;
}

export interface SearchDoneEvent {
  readonly type: 'done';
  readonly elapsed_ms: number | null;
  readonly error: { readonly status: number | null; readonly detail: string; readonly code: string | null } | null;
}

export type SearchEvent = SearchTierEvent | SearchDoneEvent;

export interface RetrieveItem {
  readonly entry: EntrySummary;
  readonly score: number;
  readonly passages: readonly string[];
}

export interface RetrieveResult {
  readonly items: readonly RetrieveItem[];
  readonly unavailable: Unavailable | null;
}

export const QUEUE_ITEM_STATUSES = ['queued', 'running', 'failed'] as const;
export type QueueItemStatus = (typeof QUEUE_ITEM_STATUSES)[number];

export interface QueueItem {
  /** Null for jobs that are not tied to an entry, such as `compile`. */
  readonly entry_id: string | null;
  readonly entry_title: string | null;
  readonly entry_date: string | null;
  readonly job_id: string;
  readonly kind: string;
  readonly target: string | null;
  readonly turn: number | null;
  readonly status: QueueItemStatus;
  readonly blocked_by: readonly BlockedBy[];
  readonly last_error: string | null;
  /** ISO timestamp before which a retry will not start. */
  readonly not_before: string | null;
}

export type BatchTrigger = 'manual' | 'schedule';

/** A stable code with its message, as in problem responses (BATCH_FAILED, BATCH_LEASE_LOST, ...). */
export interface CodedDetail {
  readonly code: string;
  readonly detail: string;
}

export interface QueueSnapshot {
  readonly batch: {
    readonly running: boolean;
    readonly started_at: string | null;
    readonly trigger: BatchTrigger | null;
    readonly last_finished_at: string | null;
    readonly last_result: { readonly processed: number; readonly failed: number } | null;
    readonly last_error: CodedDetail | null;
    /** A manual request is recorded and waits for the worker to pick it up. */
    readonly request_pending: boolean;
  };
  readonly worker: {
    /** Last heartbeat; liveness only. */
    readonly last_seen_at: string | null;
    /** Scheduled batch interval (batch_interval_s). */
    readonly interval_s: number;
    /** Next worker wake-up. */
    readonly next_run_at: string | null;
    /** Next scheduled batch. */
    readonly next_batch_at: string | null;
    /** Server-computed staleness; null when the server does not report it. */
    readonly stale: boolean | null;
  };
  readonly counts: {
    readonly queued: number;
    readonly running: number;
    readonly failed: number;
    readonly blocked: number;
  };
  readonly items: readonly QueueItem[];
}

/**
 * POST /batches/run: the worker picks the request up within a few seconds.
 * `worker.stale` is true when no worker heartbeat is recent, so nothing will run it.
 */
export interface BatchRequested {
  readonly requested: true;
  readonly worker: { readonly stale: boolean } | null;
}

export const EXPORT_FORMATS = ['text', 'markdown', 'json'] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

export interface ExportInclude {
  readonly title: boolean;
  readonly date: boolean;
  readonly tags: boolean;
  readonly summary: boolean;
  readonly text: boolean;
  readonly extraction: boolean;
}

export interface ExportRequest {
  readonly entry_ids: readonly string[];
  readonly format: ExportFormat;
  readonly include: ExportInclude;
  readonly order: SortOrder;
}

export interface ExportResult {
  readonly body: string;
  readonly contentType: string;
  readonly filename: string;
}

export interface CompileResult {
  readonly resume_bullets: readonly { readonly text: string; readonly evidence_dates: readonly string[] }[];
  readonly star_stories: readonly {
    readonly title: string;
    readonly situation: string;
    readonly task: string;
    readonly action: string;
    readonly result: string;
    readonly evidence_dates: readonly string[];
    readonly competencies: readonly string[];
  }[];
  readonly technical_topics: readonly {
    readonly topic: string;
    readonly depth_notes: string;
    readonly evidence_dates: readonly string[];
  }[];
  readonly open_threads: readonly string[];
}

export const COMPILATION_STATUSES = ['queued', 'running', 'done', 'failed'] as const;
export type CompilationStatus = (typeof COMPILATION_STATUSES)[number];

export interface Compilation {
  readonly id: string;
  readonly created_at: string;
  readonly status: CompilationStatus;
  readonly entry_ids: readonly string[];
  readonly purpose: string;
  readonly period: string;
  readonly result: CompileResult | null;
  readonly last_error: string | null;
  readonly model: string | null;
  readonly prompt_sha256: string | null;
}

export interface CompileRequest {
  readonly entry_ids: readonly string[];
  readonly purpose: string;
  readonly period: string;
}

export interface NewTextEntry {
  readonly text: string;
  readonly title: string | null;
  readonly tags: readonly string[];
  readonly entry_date: string;
}

export interface NewAudioEntry {
  readonly audio: Blob;
  /** Bare MIME type accepted by the server, for example `audio/webm`. */
  readonly mimeType: string;
  readonly fileName: string;
  readonly title: string | null;
  readonly tags: readonly string[];
  readonly entry_date: string;
}

export interface EntryPatch {
  readonly title?: string | null;
  readonly tags?: readonly string[];
  readonly entry_date?: string;
  readonly text?: string;
  /** The version the edit started from; a mismatch is answered with 409 ENTRY_CHANGED. */
  readonly expected_version?: number;
}
