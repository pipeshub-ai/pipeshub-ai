/**
 * The store's row actions that a rail can apply in place of a reload: each changes the one row in every list that
 * shows it (Your chats, Shared chats, the agent's chats, More chats) and leaves every other row as the same object.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useChatStore } from '@/chat/store';
import type { Conversation } from '@/chat/types';

const row = (id: string, lastActivityAt = 1000): Conversation => ({
  id,
  title: `Chat ${id}`,
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
  isShared: false,
  sharedWith: [],
  isOwner: true,
  lastActivityAt,
});

const ids = (rows: Conversation[]) => rows.map((c) => c.id);

beforeEach(() => {
  useChatStore.getState().reset();
  useChatStore.setState({
    conversations: [row('a', 3000), row('b', 2000), row('c', 1000)],
    sharedConversations: [row('s1', 3000), row('s2', 2000)],
    agentConversations: [row('g1', 3000), row('g2', 2000)],
  });
  const key = useChatStore.getState().startMoreChatsList();
  useChatStore.getState().setMoreChatsPage(key, [row('a', 3000), row('b', 2000), row('c', 1000)], 1, false);
});

describe('renameConversation', () => {
  it('renames the row wherever it is listed and leaves the others as they were', () => {
    const before = useChatStore.getState();

    useChatStore.getState().renameConversation('b', 'Budget');

    const s = useChatStore.getState();
    expect(s.conversations.map((c) => c.title)).toEqual(['Chat a', 'Budget', 'Chat c']);
    expect(s.moreChats.rows.map((c) => c.title)).toEqual(['Chat a', 'Budget', 'Chat c']);
    expect(s.conversations[0]).toBe(before.conversations[0]);
    expect(s.sharedConversations.map((c) => c.title)).toEqual(['Chat s1', 'Chat s2']);
  });

  it('reaches a shared chat and an agent chat too', () => {
    useChatStore.getState().renameConversation('s2', 'Shared renamed');
    useChatStore.getState().renameConversation('g1', 'Agent renamed');

    const s = useChatStore.getState();
    expect(s.sharedConversations[1].title).toBe('Shared renamed');
    expect(s.agentConversations[0].title).toBe('Agent renamed');
  });
});

describe('removeConversation', () => {
  it('takes the row out of every list and forgets its unread dot', () => {
    useChatStore.getState().markConversationUnread('b');

    useChatStore.getState().removeConversation('b');

    const s = useChatStore.getState();
    expect(ids(s.conversations)).toEqual(['a', 'c']);
    expect(ids(s.moreChats.rows)).toEqual(['a', 'c']);
    expect(s.unreadConversationIds.has('b')).toBe(false);
  });

  it('takes a shared or an agent row out of its own list', () => {
    useChatStore.getState().removeConversation('s1');
    useChatStore.getState().removeConversation('g2');

    const s = useChatStore.getState();
    expect(ids(s.sharedConversations)).toEqual(['s2']);
    expect(ids(s.agentConversations)).toEqual(['g1']);
    expect(ids(s.conversations)).toEqual(['a', 'b', 'c']);
  });
});

describe('moveConversationToTop', () => {
  it('moves the row first in its list and keeps the rest in order', () => {
    useChatStore.getState().moveConversationToTop('c');
    useChatStore.getState().moveConversationToTop('s2');
    useChatStore.getState().moveConversationToTop('g2');

    const s = useChatStore.getState();
    expect(ids(s.conversations)).toEqual(['c', 'a', 'b']);
    expect(ids(s.sharedConversations)).toEqual(['s2', 's1']);
    expect(ids(s.agentConversations)).toEqual(['g2', 'g1']);
  });

  it('changes nothing for a row already first, or not listed', () => {
    const before = useChatStore.getState();

    useChatStore.getState().moveConversationToTop('a');
    useChatStore.getState().moveConversationToTop('nowhere');

    expect(useChatStore.getState().conversations).toBe(before.conversations);
  });
});
