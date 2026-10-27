import { describe, expect, it } from 'vitest';
import type { Conversation } from '@/chat/types';
import { orderPinnedFirst, splitPinned, withPinned } from '../pinned-conversations';

function chat(id: string, lastActivityAt: number, extra: Partial<Conversation> = {}): Conversation {
  return {
    id,
    title: id,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    isShared: false,
    sharedWith: [],
    lastActivityAt,
    ...extra,
  };
}

const ids = (list: Conversation[]) => list.map((c) => c.id);

describe('withPinned', () => {
  it('pins one chat and leaves the others as they were', () => {
    const list = [chat('a', 3000), chat('b', 2000), chat('c', 1000)];

    const next = withPinned(list, 'b', true);

    expect(next.find((c) => c.id === 'b')?.isPinned).toBe(true);
    expect(next[0]).toBe(list[0]);
    expect(next[2]).toBe(list[2]);
    expect(list[1].isPinned).toBeUndefined();
  });

  it('unpins by removing the field, so the row looks like one the API never pinned', () => {
    const list = [chat('a', 3000, { isPinned: true })];

    const next = withPinned(list, 'a', false);

    expect('isPinned' in next[0]).toBe(false);
    expect(next[0]).toEqual(chat('a', 3000));
  });

  it('returns the same array when the chat is already in that state, so nothing re-renders', () => {
    const list = [chat('a', 3000, { isPinned: true }), chat('b', 2000)];

    expect(withPinned(list, 'a', true)).toBe(list);
    expect(withPinned(list, 'b', false)).toBe(list);
  });

  it('returns the same array when the chat is not in the list', () => {
    const list = [chat('a', 3000)];

    expect(withPinned(list, 'missing', true)).toBe(list);
  });
});

describe('splitPinned', () => {
  it('separates pinned chats from the rest and keeps the rest in the order given', () => {
    const list = [chat('r1', 5000), chat('p1', 1000, { isPinned: true }), chat('r2', 4000)];

    const { pinned, rest } = splitPinned(list);

    expect(ids(pinned)).toEqual(['p1']);
    expect(ids(rest)).toEqual(['r1', 'r2']);
  });

  it('puts the most recently active pinned chat first, as the API lists them', () => {
    const list = [
      chat('old', 1000, { isPinned: true }),
      chat('new', 9000, { isPinned: true }),
      chat('mid', 5000, { isPinned: true }),
    ];

    expect(ids(splitPinned(list).pinned)).toEqual(['new', 'mid', 'old']);
  });

  it('does not reorder the list it was given', () => {
    const list = [chat('old', 1000, { isPinned: true }), chat('new', 9000, { isPinned: true })];

    splitPinned(list);

    expect(ids(list)).toEqual(['old', 'new']);
  });

  it('treats only a real true as pinned', () => {
    const list = [chat('a', 1000, { isPinned: false }), chat('b', 2000)];

    expect(splitPinned(list).pinned).toEqual([]);
  });
});

describe('orderPinnedFirst', () => {
  it('lists pinned chats first and keeps the others behind them in their order', () => {
    const list = [chat('r1', 5000), chat('p1', 1000, { isPinned: true }), chat('r2', 4000), chat('p2', 3000, { isPinned: true })];

    expect(ids(orderPinnedFirst(list))).toEqual(['p2', 'p1', 'r1', 'r2']);
  });

  it('returns the same array when nothing is pinned', () => {
    const list = [chat('a', 2000), chat('b', 1000)];

    expect(orderPinnedFirst(list)).toBe(list);
  });
});
