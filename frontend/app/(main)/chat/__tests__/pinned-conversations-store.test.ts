/**
 * Pinning a chat in the owner's sidebar list: what the store changes straight
 * away, and what it leaves alone.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
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

  it('leaves shared and agent chats alone, since a pin is the owner’s own', () => {
    const shared = [chat('c2', 5000)];
    const agent = [chat('c2', 6000)];
    useChatStore.setState({ sharedConversations: shared, agentConversations: agent });

    useChatStore.getState().setConversationPinned('c2', true);

    expect(useChatStore.getState().sharedConversations).toBe(shared);
    expect(useChatStore.getState().agentConversations).toBe(agent);
    expect(owned().find((c) => c.id === 'c2')?.isPinned).toBe(true);
  });
});
