import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../store';
import type { Conversation } from '../types';

const conv = (id: string): Conversation => ({
  id,
  title: id,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  isShared: true,
  sharedWith: [],
});

beforeEach(() => useChatStore.getState().reset());

describe('moveConversationToTop with shared conversations (PH09-08)', () => {
  it('moves a shared conversation to index 0 of sharedConversations', () => {
    useChatStore.getState().setSharedConversations([conv('a'), conv('b'), conv('c')]);
    useChatStore.getState().moveConversationToTop('c');
    expect(useChatStore.getState().sharedConversations.map((c) => c.id)).toEqual(['c', 'a', 'b']);
  });

  it('leaves the owned list alone and keeps the state object when nothing moves', () => {
    useChatStore.getState().setSharedConversations([conv('a'), conv('b')]);
    useChatStore.getState().setConversations([conv('x'), conv('y')]);
    const before = useChatStore.getState();
    useChatStore.getState().moveConversationToTop('a');
    expect(useChatStore.getState()).toBe(before);
    useChatStore.getState().moveConversationToTop('y');
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['y', 'x']);
    expect(useChatStore.getState().sharedConversations.map((c) => c.id)).toEqual(['a', 'b']);
  });
});
