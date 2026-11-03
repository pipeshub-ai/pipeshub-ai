import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useChatStore } from '@/chat/store';
import type { Conversation } from '@/chat/types';
import { clearSessionCache } from '@/lib/api/session-cache';

function chat(id: string): Conversation {
  return { id, title: id, createdAt: '2026-01-01', updatedAt: '2026-01-01', isShared: false, sharedWith: [] };
}

const PAGINATION = { page: 1, limit: 10, totalCount: 1, totalPages: 1, hasNextPage: false, hasPrevPage: false };

describe('chat store at the end of a session', () => {
  beforeEach(() => useChatStore.getState().reset());
  // The toggles live in localStorage, which outlives each test.
  afterEach(() => useChatStore.getState().setAgentCapabilities({ internalSearch: true, webSearch: true }));

  it("stops the last user's running answer, so it cannot land in the next user's Recents", () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot(null);
    const controller = new AbortController();
    store.updateSlot(slotId, { isStreaming: true, abortController: controller });
    store.addPendingConversation(slotId);

    clearSessionCache();

    expect(controller.signal.aborted).toBe(true);
    // Stream handlers stop writing once their slot is gone (slotIsOnRun).
    expect(useChatStore.getState().slots).toEqual({});
    expect(useChatStore.getState().pendingConversations).toEqual({});
  });

  it('keeps the device-level search toggles as last chosen, as a page reload would', () => {
    useChatStore.getState().setAgentCapabilities({ webSearch: false });

    clearSessionCache();

    expect(useChatStore.getState().settings.agentCapabilities.webSearch).toBe(false);
  });

  it('empties Recents and forgets which filter they were fetched with', () => {
    useChatStore.getState().setSidebarLists({
      owned: { conversations: [chat('a')], pagination: PAGINATION },
      shared: { conversations: [chat('s')], pagination: PAGINATION },
      excludeProjectChats: true,
    });

    clearSessionCache();

    const state = useChatStore.getState();
    expect(state.conversations).toEqual([]);
    expect(state.sharedConversations).toEqual([]);
    expect(state.pagination).toBeNull();
    expect(state.sidebarListsExcludeProjectChats).toBeNull();
  });
});
