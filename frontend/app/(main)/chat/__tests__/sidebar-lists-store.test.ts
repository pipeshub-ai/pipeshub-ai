/**
 * The rails' lists live in the store and outlive the rail that shows them, so a rail mounted again shows them at
 * once. They go only with the person: signing out (or someone else signing in) clears every one.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useChatStore } from '@/chat/store';
import type { Conversation } from '@/chat/types';

const row = (id: string, title = `Chat ${id}`): Conversation => ({
  id,
  title,
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
  isShared: false,
  sharedWith: [],
  isOwner: true,
});

const ids = (rows: Conversation[]) => rows.map((c) => c.id);

beforeEach(() => useChatStore.getState().reset());

describe('clearSidebarLists', () => {
  it('forgets every list a rail shows, and that they were ever loaded', () => {
    useChatStore.setState({
      conversations: [row('a')],
      sharedConversations: [row('s')],
      pagination: { page: 1, limit: 10, totalCount: 1, totalPages: 1, hasNextPage: false, hasPrevPage: false },
      conversationsLoadedAt: 123,
      conversationsError: 'boom',
      isMoreChatsPanelOpen: true,
      moreChatsSectionType: 'your',
      agentConversations: [row('g')],
      agentSharedConversations: [row('h')],
      agentConversationsAgentId: 'agent-1',
      projects: [{ _id: 'p1' } as never],
    });
    useChatStore.getState().setMoreChatsPage(useChatStore.getState().moreChats.key, [row('m')], 1, true);
    const moreKey = useChatStore.getState().moreChats.key;

    useChatStore.getState().clearSidebarLists();

    const s = useChatStore.getState();
    expect(s.conversations).toEqual([]);
    expect(s.sharedConversations).toEqual([]);
    expect(s.pagination).toBeNull();
    expect(s.conversationsLoadedAt).toBeNull();
    expect(s.conversationsError).toBeNull();
    expect(s.isMoreChatsPanelOpen).toBe(false);
    expect(s.moreChats.rows).toEqual([]);
    // A page for the old list lands nowhere.
    expect(s.moreChats.key).not.toBe(moreKey);
    expect(s.agentConversations).toEqual([]);
    expect(s.agentSharedConversations).toEqual([]);
    expect(s.agentConversationsAgentId).toBeNull();
    expect(s.projects).toEqual([]);
  });

  it("turns every rail's skeleton off: a load still out for the last person must not leave one on", () => {
    useChatStore.setState({ isConversationsLoading: true, isAgentConversationsLoading: true, isProjectsLoading: true });

    useChatStore.getState().clearSidebarLists();

    const s = useChatStore.getState();
    expect([s.isConversationsLoading, s.isAgentConversationsLoading, s.isProjectsLoading]).toEqual([false, false, false]);
  });
});

describe('the agent rail keeps its lists for the same agent', () => {
  beforeEach(() => {
    useChatStore.getState().setAgentSidebarAgentId('agent-1');
    useChatStore.setState({
      agentConversations: [row('a1')],
      agentSharedConversations: [row('a2')],
      agentConversationsAgentId: 'agent-1',
    });
  });

  it("leaving the agent keeps its lists and whose they are, and leaves the chat page's agent fields alone", () => {
    useChatStore.setState({ agentStreamTools: ['t'], agentContextDisplayName: 'Agent one' });

    useChatStore.getState().setAgentSidebarAgentId(null);

    const s = useChatStore.getState();
    expect(s.agentSidebarAgentId).toBeNull();
    expect(ids(s.agentConversations)).toEqual(['a1']);
    expect(ids(s.agentSharedConversations)).toEqual(['a2']);
    expect(s.agentConversationsAgentId).toBe('agent-1');
    // The chat page sets and clears these from the agent detail.
    expect(s.agentStreamTools).toEqual(['t']);
    expect(s.agentContextDisplayName).toBe('Agent one');
  });

  it('coming back to the same agent keeps them', () => {
    useChatStore.getState().setAgentSidebarAgentId(null);
    useChatStore.getState().setAgentSidebarAgentId('agent-1');

    expect(ids(useChatStore.getState().agentConversations)).toEqual(['a1']);
  });

  it('another agent starts with nothing listed', () => {
    useChatStore.getState().setAgentSidebarAgentId(null);
    useChatStore.getState().setAgentSidebarAgentId('agent-2');

    const s = useChatStore.getState();
    expect(s.agentConversations).toEqual([]);
    expect(s.agentSharedConversations).toEqual([]);
    expect(s.agentConversationsAgentId).toBeNull();
  });

  it("a delete or a rename reaches the agent's shared chats too", () => {
    useChatStore.getState().renameConversation('a2', 'Renamed');
    expect(useChatStore.getState().agentSharedConversations[0].title).toBe('Renamed');

    useChatStore.getState().removeConversation('a2');
    expect(useChatStore.getState().agentSharedConversations).toEqual([]);
  });
});

describe('the More chats list says when page 1 arrived', () => {
  it('page 1 stamps the time; a later page keeps it', () => {
    const key = useChatStore.getState().startMoreChatsList();
    expect(useChatStore.getState().moreChats.loadedAt).toBe(0);

    useChatStore.getState().setMoreChatsPage(key, [row('a')], 1, true);
    const stamped = useChatStore.getState().moreChats.loadedAt;
    expect(stamped).toBeGreaterThan(0);

    useChatStore.getState().setMoreChatsPage(key, [row('b')], 2, false);
    expect(useChatStore.getState().moreChats.loadedAt).toBe(stamped);
  });
});
