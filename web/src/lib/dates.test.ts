import { describe, expect, it } from 'vitest';
import { addDays, addMonths, isIsoDate, presetBounds, weekday } from './dates';

describe('dates', () => {
  it('validates real calendar dates only', () => {
    expect(isIsoDate('2028-02-29')).toBe(true);
    expect(isIsoDate('2026-02-29')).toBe(false);
    expect(isIsoDate('2026-1-05')).toBe(false);
  });

  it('steps across month and year ends', () => {
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
    expect(addMonths('2026-03-31', -1)).toBe('2026-02-28');
  });

  it('computes inclusive preset ranges ending today', () => {
    expect(presetBounds('week', '2026-11-08')).toEqual({ from: '2026-11-02', to: '2026-11-08' });
    expect(presetBounds('month', '2026-11-08')).toEqual({ from: '2026-10-09', to: '2026-11-08' });
    expect(presetBounds('month', '2026-03-31')).toEqual({ from: '2026-03-01', to: '2026-03-31' });
    expect(presetBounds('all', '2026-11-08')).toEqual({ from: null, to: null });
  });

  it('names weekdays independently of the local zone', () => {
    expect(weekday('2026-11-08')).toBe('Sun');
  });
});
