/**
 * Opening a chat from More moves it up in the rail, as before, but keeps its time: opening a chat is not activity, so
 * Pinned and Your chats keep it where the server lists it.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { Conversation } from '@/chat/types';

installMemoryStorage();

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
const chatApi = vi.hoisted(() => ({ fetchConversations: vi.fn() }));
vi.mock('@/chat/api', () => ({ ChatApi: chatApi }));
const agentsApi = vi.hoisted(() => ({ fetchAgentConversations: vi.fn() }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: agentsApi }));

const { useChatStore } = await import('@/chat/store');
const { MoreChatsSidebar } = await import('../more-chats-sidebar');
const { AgentMoreChatsSidebar } = await import('../agent-more-chats-sidebar');

const initialState = useChatStore.getState();
const DAY = 24 * 60 * 60 * 1000;
const PAGE = { page: 1, limit: 20, totalCount: 3, totalPages: 1, hasNextPage: false, hasPrevPage: false };
const now = Date.now();

const chat = (id: string, lastActivityAt: number): Conversation => ({
  id,
  title: `Chat ${id}`,
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
  isShared: false,
  sharedWith: [],
  isOwner: true,
  lastActivityAt,
});

// As the server lists them, newest activity first. c is a week old.
const ROWS = [chat('a', now - 1000), chat('b', now - 2000), chat('c', now - 7 * DAY)];

beforeEach(() => {
  installBrowserShims();
  router.push.mockReset();
  chatApi.fetchConversations.mockResolvedValue({ conversations: ROWS, pagination: PAGE });
  agentsApi.fetchAgentConversations.mockResolvedValue({ conversations: ROWS, sharedConversations: [], pagination: PAGE });
  useChatStore.setState({
    ...initialState,
    conversations: ROWS,
    sharedConversations: [],
    agentConversations: ROWS,
    isConversationsLoading: false,
  });
});
afterEach(() => cleanup());

describe.each([
  ['Your chats', 'conversations', () => <MoreChatsSidebar sectionType="your" onBack={vi.fn()} />],
  ["an agent's chats", 'agentConversations', () => <AgentMoreChatsSidebar agentId="g1" onBack={vi.fn()} />],
] as const)("opening a chat from %s' More", (_, list, panel) => {
  it('moves it up in the rail but keeps its time', async () => {
    renderInTheme(panel());

    fireEvent.click(await screen.findByText('Chat c'));

    expect(router.push).toHaveBeenCalledTimes(1);
    const rows = useChatStore.getState()[list];
    expect(rows.map((c) => c.id)).toEqual(['c', 'a', 'b']);
    expect(rows[0]).toBe(ROWS[2]);
  });
});
