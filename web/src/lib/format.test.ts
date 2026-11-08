import { describe, expect, it } from 'vitest';
import type { EntrySummary } from '../api/types';
import { entryPlainText, headline, stageLabel } from './format';

const ENTRY: EntrySummary = {
  id: 'e1',
  entry_date: '2026-11-08',
  created_at: '2026-11-08T14:03:11Z',
  title: 'Index rebuild',
  tags: ['work', 'on-call'],
  source: 'text',
  summary: 'Rebuilt the index.',
  text: 'First paragraph.\n\nSecond paragraph.',
  projects: [],
  skills: [],
  status: 'done',
  revision: 1,
  version: 1,
};

describe('format', () => {
  it('copies an entry in the plain-text export layout', () => {
    expect(entryPlainText(ENTRY)).toBe(
      '2026-11-08  Index rebuild\n\nSummary:\nRebuilt the index.\n\nFirst paragraph.\n\nSecond paragraph.\n\nTags:\nwork, on-call',
    );
    expect(entryPlainText({ ...ENTRY, title: null, summary: null, tags: [] })).toBe('2026-11-08\n\nFirst paragraph.\n\nSecond paragraph.');
  });

  it('falls back from title to summary to first line', () => {
    expect(headline({ title: '  ', summary: null, text: 'Line one\nLine two' })).toBe('Line one');
  });

  it('names review stages with their target and turn', () => {
    expect(stageLabel('review', 'summarize', 2)).toBe('Review summary, turn 2');
    expect(stageLabel('embed', null, null)).toBe('Embed for search');
  });
});
