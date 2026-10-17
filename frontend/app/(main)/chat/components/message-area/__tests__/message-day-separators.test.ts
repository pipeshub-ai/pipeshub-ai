import { describe, it, expect, vi, afterEach } from 'vitest';
import { findGroupStarts, GROUP_LABEL_KEY } from '../message-day-separators';
import { groupByTime, TIME_GROUP_KEYS } from '@/lib/utils/group-by-time';
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

// Thursday 15 Oct 2026, midday. Today = 15th, yesterday = 14th,
// "Previous 7 Days" = 7th..13th, anything before the 7th is "Older".
const now = new Date(2026, 9, 15, 12, 0);

const groupOf = (iso: string) => findGroupStarts([pair('a', iso)], now).get('a');

describe('findGroupStarts — which group a message falls in', () => {
  it('calls anything from today "Today", from the first minute to the last', () => {
    expect(groupOf(local(2026, 10, 15, 0, 0))).toBe('Today');
    expect(groupOf(local(2026, 10, 15, 23, 59))).toBe('Today');
  });

  it('calls anything from yesterday "Yesterday"', () => {
    expect(groupOf(local(2026, 10, 14, 0, 0))).toBe('Yesterday');
    expect(groupOf(local(2026, 10, 14, 23, 59))).toBe('Yesterday');
  });

  it('puts the seven days before yesterday in "Previous 7 Days"', () => {
    expect(groupOf(local(2026, 10, 13, 23, 59))).toBe('Previous 7 Days');
    expect(groupOf(local(2026, 10, 10))).toBe('Previous 7 Days');
    expect(groupOf(local(2026, 10, 7, 0, 0))).toBe('Previous 7 Days');
  });

  it('puts everything before that in "Older", never a date', () => {
    expect(groupOf(local(2026, 10, 6, 23, 59))).toBe('Older');
    expect(groupOf(local(2026, 9, 1))).toBe('Older');
    expect(groupOf(local(2025, 12, 31))).toBe('Older');
  });

  it('counts "yesterday" across a month end', () => {
    const firstOfMonth = new Date(2026, 10, 1, 9, 0);
    expect(findGroupStarts([pair('a', local(2026, 10, 31))], firstOfMonth).get('a')).toBe('Yesterday');
  });

  it('counts "Previous 7 Days" across a month end', () => {
    const fifthOfMonth = new Date(2026, 10, 5, 9, 0);
    expect(findGroupStarts([pair('a', local(2026, 10, 29))], fifthOfMonth).get('a')).toBe('Previous 7 Days');
    expect(findGroupStarts([pair('a', local(2026, 10, 27))], fifthOfMonth).get('a')).toBe('Older');
  });

  it('counts "Yesterday" across a year end', () => {
    const newYearsDay = new Date(2027, 0, 1, 9, 0);
    expect(findGroupStarts([pair('a', local(2026, 12, 31))], newYearsDay).get('a')).toBe('Yesterday');
  });

  it('uses the same boundaries as the sidebar', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    try {
      for (let daysAgo = 0; daysAgo <= 20; daysAgo += 1) {
        for (const hour of [0, 12, 23]) {
          const at = new Date(2026, 9, 15 - daysAgo, hour, 30);
          const sidebar = TIME_GROUP_KEYS.find(
            (key) => groupByTime([at.getTime()], (ms) => ms)[key]?.length === 1,
          );
          expect(findGroupStarts([pair('a', at.toISOString())], now).get('a')).toBe(sidebar);
        }
      }
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('findGroupStarts — where the labels go', () => {
  it('marks the first message and each message that opens a new group', () => {
    const pairs = [
      pair('a', local(2026, 9, 1)),
      pair('b', local(2026, 9, 20)),
      pair('c', local(2026, 10, 9)),
      pair('d', local(2026, 10, 12)),
      pair('e', local(2026, 10, 14)),
      pair('f', local(2026, 10, 15, 9)),
      pair('g', local(2026, 10, 15, 10)),
    ];
    const starts = findGroupStarts(pairs, now);
    expect([...starts.keys()]).toEqual(['a', 'c', 'e', 'f']);
    expect([...starts.values()]).toEqual(['Older', 'Previous 7 Days', 'Yesterday', 'Today']);
  });

  it('shows one label for many different days in the same group', () => {
    const pairs = [
      pair('a', local(2026, 10, 7)),
      pair('b', local(2026, 10, 9)),
      pair('c', local(2026, 10, 11)),
      pair('d', local(2026, 10, 13)),
    ];
    expect([...findGroupStarts(pairs, now).keys()]).toEqual(['a']);
  });

  it('shows one "Older" label however long ago the messages are', () => {
    const pairs = [
      pair('a', local(2024, 1, 3)),
      pair('b', local(2025, 6, 20)),
      pair('c', local(2026, 9, 30)),
    ];
    expect([...findGroupStarts(pairs, now).keys()]).toEqual(['a']);
  });

  it('marks nothing for an empty list', () => {
    expect(findGroupStarts([], now).size).toBe(0);
  });

  it('never marks a message without a timestamp, and does not split the group it sits in', () => {
    const pairs = [
      pair('a', local(2026, 10, 15, 9)),
      pair('b', undefined),
      pair('c', local(2026, 10, 15, 11)),
    ];
    expect([...findGroupStarts(pairs, now).keys()]).toEqual(['a']);
  });

  it('skips messages with an unparsable timestamp', () => {
    expect(findGroupStarts([pair('a', 'garbage')], now).size).toBe(0);
  });

  it('starts the first dated message even when undated ones come before it', () => {
    const pairs = [pair('a', undefined), pair('b', local(2026, 10, 15))];
    expect([...findGroupStarts(pairs, now).keys()]).toEqual(['b']);
  });

  it('marks a return to a more recent group as a new group', () => {
    const pairs = [pair('a', local(2026, 10, 15)), pair('b', local(2026, 10, 14))];
    expect([...findGroupStarts(pairs, now).keys()]).toEqual(['a', 'b']);
  });
});

describe('GROUP_LABEL_KEY', () => {
  it('has a label for every group', () => {
    expect(Object.keys(GROUP_LABEL_KEY).sort()).toEqual([...TIME_GROUP_KEYS].sort());
  });
});

afterEach(() => {
  vi.useRealTimers();
});
