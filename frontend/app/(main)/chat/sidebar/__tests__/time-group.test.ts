import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Conversation } from '@/chat/types';
import { groupConversationsWithPinned } from '../time-group';

const NOW = new Date(2026, 9, 19, 14, 0, 0).getTime();
const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

function chat(id: string, ageMs: number, extra: Partial<Conversation> = {}): Conversation {
  return {
    id,
    title: id,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    isShared: false,
    sharedWith: [],
    lastActivityAt: NOW - ageMs,
    ...extra,
  };
}

const shape = (groups: ReturnType<typeof groupConversationsWithPinned>) =>
  groups.map(([label, list]) => [label, list.map((c) => c.id)]);

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

describe('groupConversationsWithPinned', () => {
  it('has no Pinned group when nothing is pinned, and groups by time as before', () => {
    const groups = groupConversationsWithPinned([chat('today', HOUR), chat('yday', DAY), chat('old', 30 * DAY)]);

    expect(shape(groups)).toEqual([
      ['Today', ['today']],
      ['Yesterday', ['yday']],
      ['Older', ['old']],
    ]);
  });

  it('puts pinned chats in a Pinned group above every day group', () => {
    const groups = groupConversationsWithPinned([
      chat('today', HOUR),
      chat('pinned-old', 30 * DAY, { isPinned: true }),
    ]);

    expect(shape(groups)).toEqual([
      ['Pinned', ['pinned-old']],
      ['Today', ['today']],
    ]);
  });

  it('shows a pinned chat once, under Pinned, not again under the day it was last active', () => {
    const groups = groupConversationsWithPinned([chat('pinned-today', HOUR, { isPinned: true }), chat('other', 2 * HOUR)]);

    expect(shape(groups)).toEqual([
      ['Pinned', ['pinned-today']],
      ['Today', ['other']],
    ]);
  });

  it('orders the pinned chats newest activity first', () => {
    const groups = groupConversationsWithPinned([
      chat('oldest', 9 * DAY, { isPinned: true }),
      chat('newest', HOUR, { isPinned: true }),
    ]);

    expect(shape(groups)).toEqual([['Pinned', ['newest', 'oldest']]]);
  });

  it('has only the Pinned group when every chat is pinned', () => {
    const groups = groupConversationsWithPinned([chat('a', HOUR, { isPinned: true })]);

    expect(shape(groups)).toEqual([['Pinned', ['a']]]);
  });

  it('is empty for no chats', () => {
    expect(groupConversationsWithPinned([])).toEqual([]);
  });
});
