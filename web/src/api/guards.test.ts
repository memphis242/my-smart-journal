import { describe, expect, it } from 'vitest';
import { entryDetail, queueSnapshot, ShapeError } from './guards';

const SUMMARY = {
  id: 'e1',
  entry_date: '2026-11-08',
  created_at: '2026-11-08T14:03:11Z',
  title: null,
  tags: ['work'],
  source: 'audio',
  summary: null,
  text: '',
  projects: ['Beacon'],
  skills: [],
  status: 'failed',
  revision: 1,
};

describe('guards', () => {
  it('accepts a detail with SQLite-style flags and drops fields the UI does not use', () => {
    const detail = entryDetail(
      {
        ...SUMMARY,
        needs_attention: 0,
        compilation_count: 2,
        artifacts: [{ id: 'a1', kind: 'raw_audio', content: 'audio/ab.webm', model: null, created_at: 'x', revision: 1, needs_attention: 0, target: null, turn: 0, superseded_by: null }],
        jobs: [{ id: 'j1', kind: 'transcribe', status: 'failed', attempts: 3, last_error: 'HTTP 413', created_at: 'x', finished_at: null, revision: 1, not_before: 1762600000.5, blocked_by: [] }],
        review_sessions: { cleanup: { status: 'pending', turns_used: 0, remaining_claims: [], history: [] } },
      },
      '',
    );
    expect(detail.needs_attention).toBe(false);
    expect(detail.compilation_count).toBe(2);
    expect(detail.jobs[0]?.target).toBeNull();
    expect(detail.review_sessions.cleanup?.status).toBe('pending');
    expect('superseded_by' in (detail.artifacts[0] ?? {})).toBe(false);
  });

  it('turns an epoch not_before into an ISO timestamp', () => {
    const snapshot = queueSnapshot(
      {
        batch: { running: true, started_at: 'x', trigger: 'manual', last_finished_at: null, last_result: null, last_error: { code: 'BATCH_FAILED', detail: 'boom' } },
        worker: { last_seen_at: null, interval_s: 900, next_run_at: null },
        counts: { queued: 1, running: 0, failed: 0, blocked: 0 },
        items: [{ entry_id: null, entry_title: null, entry_date: null, job_id: 'c1', kind: 'compile', target: null, turn: null, status: 'queued', blocked_by: [], last_error: null, not_before: 0 }],
      },
      '',
    );
    expect(snapshot.items[0]?.not_before).toBe('1970-01-01T00:00:00.000Z');
    expect(snapshot.batch.last_error?.code).toBe('BATCH_FAILED');
  });

  it('names the path and the found value on a mismatch', () => {
    const error = (() => {
      try {
        entryDetail({ ...SUMMARY, status: 'pending' }, '');
        return null;
      } catch (caught) {
        return caught;
      }
    })();
    expect(error).toBeInstanceOf(ShapeError);
    expect((error as ShapeError).path).toBe('status');
    expect((error as ShapeError).found).toBe('string "pending"');
  });
});
