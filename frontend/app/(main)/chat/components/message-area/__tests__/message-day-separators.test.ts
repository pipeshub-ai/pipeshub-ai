import { describe, it, expect } from 'vitest';
import { findDayStarts, getDayLabel, localDayKey } from '../message-day-separators';
import type { MessagePair } from '../message-pairs';

function pair(key: string, createdAt?: string): MessagePair {
  return {
    key,
    question: key,
    answer: '',
    citationMaps: {} as MessagePair['citationMaps'],
    isStreaming: false,
    createdAt,
  };
}

const local = (y: number, m: number, d: number, h = 12, min = 0) =>
  new Date(y, m - 1, d, h, min).toISOString();

describe('localDayKey', () => {
  it('gives the same key for two times on one local day', () => {
    expect(localDayKey(local(2026, 10, 15, 0, 1))).toBe(localDayKey(local(2026, 10, 15, 23, 59)));
  });

  it('gives different keys for neighbouring days, including across a month end', () => {
    expect(localDayKey(local(2026, 10, 31))).not.toBe(localDayKey(local(2026, 11, 1)));
  });

  it('does not confuse the same day number in different months or years', () => {
    expect(localDayKey(local(2026, 1, 11))).not.toBe(localDayKey(local(2026, 11, 1)));
    expect(localDayKey(local(2025, 10, 15))).not.toBe(localDayKey(local(2026, 10, 15)));
  });

  it.each([undefined, '', 'not a date'])('returns null for %j', (value) => {
    expect(localDayKey(value as string | undefined)).toBeNull();
  });
});

describe('findDayStarts', () => {
  it('marks the first message and each message that opens a new day', () => {
    const pairs = [
      pair('a', local(2026, 10, 13, 9)),
      pair('b', local(2026, 10, 13, 17)),
      pair('c', local(2026, 10, 14, 9)),
      pair('d', local(2026, 10, 15, 9)),
      pair('e', local(2026, 10, 15, 10)),
    ];
    expect([...findDayStarts(pairs).keys()]).toEqual(['a', 'c', 'd']);
  });

  it('returns the timestamp that opens the day', () => {
    const at = local(2026, 10, 13, 9);
    expect(findDayStarts([pair('a', at)]).get('a')).toBe(at);
  });

  it('marks nothing for an empty list', () => {
    expect(findDayStarts([]).size).toBe(0);
  });

  it('never marks a message without a timestamp, and does not split the day it sits in', () => {
    const pairs = [
      pair('a', local(2026, 10, 15, 9)),
      pair('b', undefined),
      pair('c', local(2026, 10, 15, 11)),
    ];
    expect([...findDayStarts(pairs).keys()]).toEqual(['a']);
  });

  it('skips messages with an unparsable timestamp', () => {
    expect(findDayStarts([pair('a', 'garbage')]).size).toBe(0);
  });

  it('starts the first dated message even when undated ones come before it', () => {
    const pairs = [pair('a', undefined), pair('b', local(2026, 10, 15))];
    expect([...findDayStarts(pairs).keys()]).toEqual(['b']);
  });

  it('marks a return to an earlier calendar day as a new day', () => {
    const pairs = [pair('a', local(2026, 10, 15)), pair('b', local(2026, 10, 14))];
    expect([...findDayStarts(pairs).keys()]).toEqual(['a', 'b']);
  });
});

describe('getDayLabel', () => {
  const now = new Date(2026, 9, 15, 10, 30);

  it('calls the current local day Today, even late at night', () => {
    expect(getDayLabel(local(2026, 10, 15, 0, 5), 'en-US', now)).toEqual({ kind: 'today' });
    expect(getDayLabel(local(2026, 10, 15, 23, 59), 'en-US', now)).toEqual({ kind: 'today' });
  });

  it('calls the day before Yesterday', () => {
    expect(getDayLabel(local(2026, 10, 14, 23, 59), 'en-US', now)).toEqual({ kind: 'yesterday' });
    expect(getDayLabel(local(2026, 10, 14, 0, 0), 'en-US', now)).toEqual({ kind: 'yesterday' });
  });

  it('finds Yesterday across a month boundary', () => {
    const firstOfMonth = new Date(2026, 10, 1, 8);
    expect(getDayLabel(local(2026, 10, 31), 'en-US', firstOfMonth)).toEqual({ kind: 'yesterday' });
  });

  it('shows a short date, without the year, for earlier days this year', () => {
    expect(getDayLabel(local(2026, 10, 13), 'en-US', now)).toEqual({ kind: 'date', text: 'Oct 13' });
  });

  it('adds the year for days in other years', () => {
    expect(getDayLabel(local(2025, 12, 31), 'en-US', now)).toEqual({ kind: 'date', text: 'Dec 31, 2025' });
  });

  it('formats the date in the reader’s language', () => {
    const label = getDayLabel(local(2026, 10, 13), 'de-DE', now);
    expect(label).toEqual({ kind: 'date', text: '13. Okt.' });
  });

  it('treats a future timestamp as a plain date, not Today', () => {
    expect(getDayLabel(local(2026, 10, 20), 'en-US', now)).toEqual({ kind: 'date', text: 'Oct 20' });
  });

  it('returns null for an unparsable timestamp', () => {
    expect(getDayLabel('nope', 'en-US', now)).toBeNull();
  });
});
