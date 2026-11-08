/**
 * Calendar dates are `YYYY-MM-DD` strings in the user's local calendar.
 * Arithmetic runs in UTC on those strings so daylight-saving shifts cannot
 * skip or repeat a day. Times are shown in 24-hour local form.
 */

import { assert } from './assert';

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

function pad(value: number, width = 2): string {
  return String(value).padStart(width, '0');
}

/** True for a real calendar date written `YYYY-MM-DD`. */
export function isIsoDate(value: string): boolean {
  const match = ISO_DATE.exec(value);
  if (match === null) return false;
  const [, y, m, d] = match;
  const date = new Date(Date.UTC(Number(y), Number(m) - 1, Number(d)));
  return date.getUTCFullYear() === Number(y) && date.getUTCMonth() === Number(m) - 1 && date.getUTCDate() === Number(d);
}

function toUtc(iso: string): Date {
  assert(isIsoDate(iso), `calendar date: ${iso}`);
  const [y, m, d] = iso.split('-').map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d));
}

function fromUtc(date: Date): string {
  return `${pad(date.getUTCFullYear(), 4)}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

export function localDate(at: Date = new Date()): string {
  return `${pad(at.getFullYear(), 4)}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`;
}

export function addDays(iso: string, days: number): string {
  const date = toUtc(iso);
  date.setUTCDate(date.getUTCDate() + days);
  return fromUtc(date);
}

/** Same day `months` months away, clamped to the end of a shorter month. */
export function addMonths(iso: string, months: number): string {
  const date = toUtc(iso);
  const day = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + months);
  const lastDay = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
  date.setUTCDate(Math.min(day, lastDay));
  return fromUtc(date);
}

export function weekday(iso: string): string {
  const name = WEEKDAYS[toUtc(iso).getUTCDay()];
  assert(name !== undefined, 'weekday index in range');
  return name;
}

export function daysBetween(fromIso: string, toIso: string): number {
  return Math.round((toUtc(toIso).getTime() - toUtc(fromIso).getTime()) / 86_400_000);
}

function parseTimestamp(value: string): Date | null {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** `HH:MM` local, or the raw value when it is not a timestamp. */
export function formatTime(value: string, seconds = false): string {
  const date = parseTimestamp(value);
  if (date === null) return value;
  const base = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return seconds ? `${base}:${pad(date.getSeconds())}` : base;
}

/** `YYYY-MM-DD HH:MM` local. */
export function formatDateTime(value: string, seconds = false): string {
  const date = parseTimestamp(value);
  if (date === null) return value;
  return `${localDate(date)} ${formatTime(value, seconds)}`;
}

/** Local date of a timestamp, or null when it does not parse. */
export function timestampDate(value: string): string | null {
  const date = parseTimestamp(value);
  return date === null ? null : localDate(date);
}

/** Time of day when the timestamp is today, else date and time. */
export function formatWhen(value: string, now: Date = new Date()): string {
  const date = parseTimestamp(value);
  if (date === null) return value;
  return localDate(date) === localDate(now) ? formatTime(value) : formatDateTime(value);
}

/** Exact distance such as "in 12 min", "3 h 5 min ago". */
export function formatDistance(value: string, now: Date = new Date()): string {
  const date = parseTimestamp(value);
  if (date === null) return value;
  const deltaS = Math.round((date.getTime() - now.getTime()) / 1000);
  const abs = Math.abs(deltaS);
  let text: string;
  if (abs < 60) text = `${abs} s`;
  else if (abs < 3600) text = `${Math.round(abs / 60)} min`;
  else if (abs < 86_400) {
    const hours = Math.floor(abs / 3600);
    const minutes = Math.round((abs % 3600) / 60);
    text = minutes === 0 ? `${hours} h` : `${hours} h ${minutes} min`;
  } else {
    text = `${Math.round(abs / 86_400)} d`;
  }
  return deltaS >= 0 ? `in ${text}` : `${text} ago`;
}

export const RANGE_KEYS = ['today', 'week', 'month', 'year', 'all', 'custom'] as const;
export type RangeKey = (typeof RANGE_KEYS)[number];

export interface DateBounds {
  readonly from: string | null;
  readonly to: string | null;
}

/** Inclusive bounds for a preset, ending today. */
export function presetBounds(key: Exclude<RangeKey, 'custom'>, today: string): DateBounds {
  switch (key) {
    case 'today':
      return { from: today, to: today };
    case 'week':
      return { from: addDays(today, -6), to: today };
    case 'month':
      return { from: addDays(addMonths(today, -1), 1), to: today };
    case 'year':
      return { from: addDays(addMonths(today, -12), 1), to: today };
    case 'all':
      return { from: null, to: null };
  }
}

export function describeBounds(bounds: DateBounds): string {
  if (bounds.from === null && bounds.to === null) return 'all dates';
  if (bounds.from !== null && bounds.to !== null) {
    return bounds.from === bounds.to ? bounds.from : `${bounds.from} to ${bounds.to}`;
  }
  return bounds.from !== null ? `from ${bounds.from}` : `until ${bounds.to ?? ''}`;
}
