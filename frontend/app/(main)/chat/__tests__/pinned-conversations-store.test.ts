/**
 * Pinning a chat in the owner's sidebar list: what the store changes straight
 * away, and what it leaves alone.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { getConversationLastActivityMs } from '@/lib/utils/conversation-activity';
import { splitPinned } from '../utils/pinned-conversations';
import type { Conversation } from '../types';

installMemoryStorage();

const { useChatStore } = await import('../store');

const initialState = useChatStore.getState();

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

const owned = () => useChatStore.getState().conversations;
const ids = (list: Conversation[]) => list.map((c) => c.id);

beforeEach(() => {
  useChatStore.setState({
    ...initialState,
    conversations: [chat('c3', 3000), chat('c2', 2000), chat('c1', 1000)],
    sharedConversations: [],
    agentConversations: [],
  });
});

describe('setConversationPinned', () => {
  it('pins a chat and lists it first, ahead of newer chats', () => {
    useChatStore.getState().setConversationPinned('c1', true);

    expect(ids(owned())).toEqual(['c1', 'c3', 'c2']);
    expect(owned()[0].isPinned).toBe(true);
  });

  it('keeps pinned chats newest first among themselves', () => {
    useChatStore.getState().setConversationPinned('c1', true);
    useChatStore.getState().setConversationPinned('c3', true);

    expect(ids(owned())).toEqual(['c3', 'c1', 'c2']);
  });

  it('unpins a chat back to where its last activity puts it, not to the top of the rest', () => {
    useChatStore.getState().setConversationPinned('c1', true);
    expect(ids(owned())).toEqual(['c1', 'c3', 'c2']);

    useChatStore.getState().setConversationPinned('c1', false);

    expect(ids(owned())).toEqual(['c3', 'c2', 'c1']);
  });

  it('unpins a chat and removes the field, so it matches what the API sends for an unpinned chat', () => {
    useChatStore.getState().setConversationPinned('c1', true);

    useChatStore.getState().setConversationPinned('c1', false);

    const row = owned().find((c) => c.id === 'c1');
    expect(row && 'isPinned' in row).toBe(false);
  });

  it('does nothing, and does not replace the list, for a chat that is already in that state', () => {
    const before = owned();

    useChatStore.getState().setConversationPinned('c2', false);
    useChatStore.getState().setConversationPinned('missing', true);

    expect(owned()).toBe(before);
  });

  it('pins a shared chat in the viewer’s own shared list too, first, and leaves agent chats alone', () => {
    const shared = [chat('c9', 1000), chat('c2', 5000)];
    const agent = [chat('c2', 6000)];
    useChatStore.setState({ sharedConversations: shared, agentConversations: agent });

    useChatStore.getState().setConversationPinned('c2', true);

    expect(useChatStore.getState().sharedConversations.map((c) => [c.id, c.isPinned === true])).toEqual([
      ['c2', true],
      ['c9', false],
    ]);
    expect(useChatStore.getState().agentConversations).toBe(agent);
    expect(owned().find((c) => c.id === 'c2')?.isPinned).toBe(true);

    useChatStore.getState().setConversationPinned('c2', false);
    expect(useChatStore.getState().sharedConversations.find((c) => c.id === 'c2')).not.toHaveProperty('isPinned');
  });

  it('its undo puts every list back as it was, rows in their places', () => {
    const shared = [chat('s1', 1000), chat('c2', 5000)];
    useChatStore.setState({ sharedConversations: shared });
    const ownBefore = useChatStore.getState().conversations;

    const undo = useChatStore.getState().setConversationPinned('c2', true);
    undo();

    expect(useChatStore.getState().conversations).toBe(ownBefore);
    expect(useChatStore.getState().sharedConversations).toBe(shared);
  });

  it('its undo after the lists changed meanwhile puts back only the pin, keeping the newer lists', () => {
    useChatStore.setState({ sharedConversations: [chat('s1', 1000), chat('c2', 5000)] });
    const undo = useChatStore.getState().setConversationPinned('c2', true);
    const refetched = [chat('s9', 9000), chat('c2', 5000, { isPinned: true }), chat('s1', 1000)];
    useChatStore.setState({ sharedConversations: refetched });

    undo();

    expect(useChatStore.getState().sharedConversations.map((c) => `${c.id}${c.isPinned ? '*' : ''}`)).toEqual(['s9', 'c2', 's1']);
  });
});

describe('moveConversationToTop, what a new message in a chat does', () => {
  const inMore = (id: string) => useChatStore.getState().moreChats.rows.find((c) => c.id === id);

  beforeEach(() => {
    const key = useChatStore.getState().startMoreChatsList();
    useChatStore.getState().setMoreChatsPage(key, owned(), 1, false);
  });

  it('gives the chat the time the server saved for it, in More as well, so Pinned, which orders by time, puts it first', () => {
    const before = owned();

    useChatStore.getState().moveConversationToTop('c1', { lastActivityAt: 4000 });

    expect(ids(owned())).toEqual(['c1', 'c3', 'c2']);
    expect(owned()[0].lastActivityAt).toBe(4000);
    expect(inMore('c1')?.lastActivityAt).toBe(4000);
    // Every other row stays the same object.
    expect(owned()[1]).toBe(before[0]);
    expect(owned()[2]).toBe(before[1]);
  });

  it('with no time from the server, makes the chat the newest by time, in More as well', () => {
    const before = owned();

    useChatStore.getState().moveConversationToTop('c1', {});

    expect(ids(owned())).toEqual(['c1', 'c3', 'c2']);
    expect(getConversationLastActivityMs(owned()[0])).toBeGreaterThan(getConversationLastActivityMs(before[0]));
    expect(inMore('c1')?.lastActivityAt).toBe(owned()[0].lastActivityAt);
    expect(owned()[1]).toBe(before[0]);
    expect(owned()[2]).toBe(before[1]);
  });

  it('takes the new time even when the chat is already first and the newest, as yesterday’s last chat is at the first message of the morning', () => {
    const DAY = 24 * 60 * 60 * 1000;
    const yesterday = Date.now() - DAY;
    useChatStore.setState({ conversations: [chat('c1', yesterday), chat('c2', yesterday - 60_000)] });

    useChatStore.getState().moveConversationToTop('c1', { lastActivityAt: yesterday + DAY });
    expect(owned()[0].lastActivityAt).toBe(yesterday + DAY);

    // With no time from the server: now.
    useChatStore.setState({ conversations: [chat('c1', yesterday), chat('c2', yesterday - 60_000)] });
    const before = Date.now();
    useChatStore.getState().moveConversationToTop('c1', {});
    expect(getConversationLastActivityMs(owned()[0])).toBeGreaterThanOrEqual(before);
    expect(inMore('c1')?.lastActivityAt).toBe(owned()[0].lastActivityAt);
  });

  it('keeps a time that is already the newest (a new message seen live sets it first), and gives it to More', () => {
    useChatStore.setState({ conversations: [chat('c3', 3000), chat('c2', 2000), chat('c1', 9000)] });
    const c1 = owned()[2];

    useChatStore.getState().moveConversationToTop('c1');

    expect(ids(owned())).toEqual(['c1', 'c3', 'c2']);
    expect(owned()[0]).toBe(c1);
    const more = inMore('c1');
    expect(more && getConversationLastActivityMs(more)).toBe(getConversationLastActivityMs(c1));
  });
});

describe('moveConversationToTop for a chat opened from More, with no new message', () => {
  const DAY = 24 * 60 * 60 * 1000;
  const now = Date.now();
  const pinnedRail = () => {
    const s = useChatStore.getState();
    return splitPinned([...s.conversations, ...s.sharedConversations]).pinned.map((c) => c.id);
  };
  const yourRail = () => owned().filter((c) => c.isPinned !== true).map((c) => c.id);

  // As the server lists them: pinned first, then newest activity first. x is a week old.
  beforeEach(() => {
    useChatStore.setState({
      conversations: [
        chat('p1', now - 1000, { isPinned: true }),
        chat('p2', now - 2000, { isPinned: true }),
        chat('z', now - 3000),
        chat('x', now - 7 * DAY),
      ],
      sharedConversations: [chat('s1', now - 1500, { isPinned: true, isOwner: false })],
    });
  });

  it('moves it up in Your chats but keeps its time: opening a chat is not activity', () => {
    const x = owned()[3];

    useChatStore.getState().moveConversationToTop('x');

    expect(ids(owned())).toEqual(['x', 'p1', 'p2', 'z']);
    expect(owned()[0]).toBe(x);
  });

  it('pinned next, it takes the place in Pinned its time gives it, as the server lists it', () => {
    useChatStore.getState().moveConversationToTop('x');
    useChatStore.getState().setConversationPinned('x', true);

    expect(pinnedRail()).toEqual(['p1', 's1', 'p2', 'x']);
  });

  it('unpinned right after, it goes back to its place in Your chats', () => {
    useChatStore.getState().moveConversationToTop('x');
    useChatStore.getState().setConversationPinned('x', true);
    useChatStore.getState().setConversationPinned('x', false);

    expect(yourRail()).toEqual(['z', 'x']);
  });
});
