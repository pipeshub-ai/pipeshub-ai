import { describe, expect, it } from 'vitest';
import {
  formatClock,
  formatDate,
  formatDateTime,
  formatDayMonth,
  formatTime,
  formatWeekdayDateTime,
  formatWeekdayName,
  formatWeekdayTime,
} from '../dates';

const AT = Date.UTC(2026, 10, 9, 15, 30);

describe('dates: one way to write a date, by the reader locale', () => {
  it('writes the same instant in each locale its own way', () => {
    expect(formatDate(AT, 'en-US')).toBe('Nov 9, 2026');
    expect(formatDate(AT, 'en-GB')).toBe('9 Nov 2026');
    expect(formatDate(new Date(AT).toISOString(), 'de-DE')).toBe('09.11.2026');
  });

  it('takes an ISO string, a number or a Date alike', () => {
    const iso = new Date(AT).toISOString();
    expect(formatDateTime(iso, 'en-US')).toBe(formatDateTime(AT, 'en-US'));
    expect(formatDateTime(new Date(AT), 'en-US')).toBe(formatDateTime(AT, 'en-US'));
  });

  it('shows nothing for an unreadable value and survives a malformed locale', () => {
    expect(formatDate('not a date', 'en-US')).toBe('');
    expect(formatDateTime(Number.NaN, 'en-US')).toBe('');
    expect(formatDate(AT, 'not_a_locale!!')).toMatch(/2026/);
  });

  it('has the compact shapes the lists use', () => {
    expect(formatDayMonth(AT, 'en-US')).toBe('Nov 9');
    expect(formatTime(AT, 'en-US')).toMatch(/\d{1,2}:30/);
    expect(formatWeekdayTime(AT, 'en-US')).toMatch(/^Mon/);
    expect(formatWeekdayDateTime(AT, 'en-US', false)).not.toMatch(/2026/);
    expect(formatWeekdayDateTime(AT, 'en-US', true)).toMatch(/2026/);
    expect(formatClock(9, 5, 'en-US')).toBe('9:05 AM');
    expect(formatWeekdayName(0, 'en-US')).toBe('Sunday');
    expect(formatWeekdayName(7, 'en-US')).toBe('Sunday');
  });
});
