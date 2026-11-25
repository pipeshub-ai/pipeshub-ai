import { beforeEach, describe, expect, it } from 'vitest';

import { useChatStore } from '@/chat/store';
import type { Conversation } from '@/chat/types';

/**
 * FIX-RAIL1: a chat still answering stays in Your chats. Both rails (the classic sidebar and the new interface's
 * rail) read `conversations`, so the store lists a new chat from the moment its id is known and a fresh server page
 * does not drop a chat this tab is still answering in.
 */

function chat(id: string, extra: Partial<Conversation> = {}): Conversation {
  return { id, title: id, createdAt: '2026-01-01', updatedAt: '2026-01-01', isShared: false, sharedWith: [], ...extra };
}

const PAGINATION = { page: 1, limit: 10, totalCount: 1, totalPages: 1, hasNextPage: false, hasPrevPage: false };

const page = (owned: Conversation[]) => ({
  owned: { conversations: owned, pagination: { ...PAGINATION, totalCount: owned.length } },
  shared: { conversations: [], pagination: PAGINATION },
  excludeProjectChats: true,
});

/** A new chat the way `streamMessageForSlot` starts one: a temp slot, streaming, its id from `connected`. */
function startNewChat(convId: string, title: string | null = 'Running chat'): string {
  const store = useChatStore.getState();
  const slotId = store.createSlot(null);
  store.updateSlot(slotId, { isTemp: true, isStreaming: true });
  store.addPendingConversation(slotId);
  useChatStore.getState().connectNewConversation(slotId, convId, title);
  return slotId;
}

describe('a chat still answering stays in Your chats', () => {
  beforeEach(() => useChatStore.getState().reset());

  it('lists a new chat as soon as its id is known, before the answer finishes', () => {
    useChatStore.getState().setSidebarLists(page([chat('old')]));
    startNewChat('live');

    const ids = useChatStore.getState().conversations.map((c) => c.id);
    expect(ids).toEqual(['live', 'old']);
    expect(useChatStore.getState().conversations[0].title).toBe('Running chat');
  });

  it('keeps the running chat when a fresh page that predates it replaces the list (New chat refetches Recents)', () => {
    useChatStore.getState().setSidebarLists(page([chat('old')]));
    startNewChat('live');

    useChatStore.getState().setSidebarLists(page([chat('old'), chat('other')]));

    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['live', 'old', 'other']);
  });

  it('keeps a chat whose durable run is still going after its first stream settled', () => {
    const slotId = startNewChat('live');
    useChatStore.getState().updateSlot(slotId, {
      isStreaming: false,
      durableRun: { runId: 'r1', sessionId: 'live', agentId: null } as never,
    });

    useChatStore.getState().setSidebarLists(page([chat('old')]));

    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['live', 'old']);
  });

  it("takes the server's row once the page has it, and drops a chat that is no longer running", () => {
    const slotId = startNewChat('live');
    useChatStore.getState().setSidebarLists(page([chat('live', { title: 'Server title' }), chat('old')]));
    expect(useChatStore.getState().conversations.find((c) => c.id === 'live')?.title).toBe('Server title');

    useChatStore.getState().updateSlot(slotId, { isStreaming: false, isTemp: false });
    useChatStore.getState().setSidebarLists(page([chat('old')]));
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['old']);
  });

  it('does not list an agent or project chat in Your chats', () => {
    const store = useChatStore.getState();
    const agentSlot = store.createSlot(null);
    store.updateSlot(agentSlot, { isTemp: true, isStreaming: true, threadAgentId: 'agent-1' });
    store.connectNewConversation(agentSlot, 'agent-chat', 'Agent chat');
    const projectSlot = store.createSlot(null);
    store.updateSlot(projectSlot, { isTemp: true, isStreaming: true, projectId: 'p1' });
    store.connectNewConversation(projectSlot, 'project-chat', 'Project chat');

    useChatStore.getState().setSidebarLists(page([chat('old')]));

    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['old']);
  });

  it('keeps the slot temp and its pending row until the answer finishes', () => {
    const slotId = startNewChat('live', null);
    const state = useChatStore.getState();
    expect(state.slots[slotId]).toMatchObject({ convId: 'live', isTemp: true });
    expect(state.pendingConversations[slotId]).toBeDefined();
    expect(state.conversations.find((c) => c.id === 'live')?.title).toBe('');
  });
});
