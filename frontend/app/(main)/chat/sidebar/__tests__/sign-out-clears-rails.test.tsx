/**
 * Sign-out without a page load (the desktop app) clears what the rails keep, also in a session that never loaded the
 * main chat lists: the rail showed only an agent's chats. Nothing in this file loads those lists first.
 */
import { describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

vi.mock('@/chat/api', () => ({ ChatApi: { fetchConversations: vi.fn() } }));

const { useChatStore } = await import('@/chat/store');
const { useAuthStore } = await import('@/config');
const { useSidebarConversations } = await import('../use-sidebar-conversations');

const b64 = (value: unknown) => btoa(JSON.stringify(value)).replace(/=+$/, '');
const tokenFor = (userId: string) => `${b64({ alg: 'HS256' })}.${b64({ userId })}.sig`;
const row = (id: string) => ({ id, title: `Chat ${id}`, createdAt: '', updatedAt: '', isShared: false, sharedWith: [] }) as never;

describe('sign-out, with only an agent rail shown', () => {
  it("clears the agent's chats and the projects, though the main lists never loaded", () => {
    act(() => useAuthStore.setState({ accessToken: tokenFor('user-a'), isAuthenticated: true } as never));
    // The rail in an agent's view: the main lists do not load.
    renderHook(() => useSidebarConversations(false));
    useChatStore.setState({
      agentConversations: [row('g')],
      agentConversationsAgentId: 'agent-1',
      projects: [{ _id: 'p1', name: 'Project p1' } as never],
      projectsLoadedAt: Date.now(),
    });

    act(() => useAuthStore.setState({ accessToken: tokenFor('user-b'), isAuthenticated: true } as never));

    const state = useChatStore.getState();
    expect(state.agentConversations).toEqual([]);
    expect(state.agentConversationsAgentId).toBeNull();
    expect(state.projects).toEqual([]);
    expect(state.projectsLoadedAt).toBeNull();
  });
});
