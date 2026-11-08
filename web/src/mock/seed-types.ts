/**
 * Shapes of the fictional seed data that the mock API serves in `pnpm dev:mock`.
 *
 * Dates are relative (`days_ago`) so that "Today", "Past week" and "Past month"
 * always have entries, whatever day the mock runs on.
 */

export interface SeedAccomplishment {
  readonly description: string;
  readonly metrics: readonly string[];
}

export interface SeedPersonRole {
  readonly name: string;
  readonly role: string;
}

export interface SeedStarFragment {
  readonly situation: string;
  readonly task: string;
  readonly action: string;
  readonly result: string;
}

export interface SeedEntry {
  /** 0 is today; the corpus spans roughly 0..160 days back. */
  readonly days_ago: number;
  /** Local wall-clock time the entry was written, "HH:MM" in 24-hour form. */
  readonly time: string;
  readonly source: 'text' | 'audio';
  readonly title: string | null;
  /** The user's own tags, lowercase. */
  readonly tags: readonly string[];
  /** As typed, or for audio, the raw speech-to-text transcript (disfluent). */
  readonly raw: string;
  /** Cleaned-up text; paragraphs separated by a blank line ("\n\n"). */
  readonly cleaned: string;
  /** One or two plain sentences. */
  readonly summary: string;
  readonly projects: readonly string[];
  readonly skills: readonly string[];
  readonly accomplishments: readonly SeedAccomplishment[];
  readonly people_roles: readonly SeedPersonRole[];
  readonly star_fragments: readonly SeedStarFragment[];
}

export interface SeedResumeBullet {
  readonly text: string;
  /** Indexes into SEED_ENTRIES whose dates support the bullet. */
  readonly evidence: readonly number[];
}

export interface SeedStarStory {
  readonly title: string;
  readonly situation: string;
  readonly task: string;
  readonly action: string;
  readonly result: string;
  readonly evidence: readonly number[];
  readonly competencies: readonly string[];
}

export interface SeedTechnicalTopic {
  readonly topic: string;
  readonly depth_notes: string;
  readonly evidence: readonly number[];
}

export interface SeedCompilation {
  /** How long ago the compilation was requested. */
  readonly days_ago: number;
  readonly purpose: string;
  readonly period: string;
  /** Indexes into SEED_ENTRIES. */
  readonly entries: readonly number[];
  readonly resume_bullets: readonly SeedResumeBullet[];
  readonly star_stories: readonly SeedStarStory[];
  readonly technical_topics: readonly SeedTechnicalTopic[];
  readonly open_threads: readonly string[];
}

/** Words that the fake semantic tier treats as related to one another. */
export interface SeedConcept {
  readonly name: string;
  readonly words: readonly string[];
}
