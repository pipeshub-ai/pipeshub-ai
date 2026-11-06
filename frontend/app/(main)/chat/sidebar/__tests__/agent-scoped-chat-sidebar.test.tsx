import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, waitFor, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const h = vi.hoisted(() => ({
  // Stable across renders: the sidebar lists `t` as a hook dependency.
  t: (key: string) => key,
  getAgent: vi.fn(),
  fetchAgentConversations: vi.fn(),
}));

const searchParams = new URLSearchParams('agentId=agent-7');
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => searchParams,
}));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: h.t, i18n: { language: 'en-US' } }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/chat-star-icon', () => ({ ChatStarIcon: () => null }));
vi.mock('@/app/components/sidebar', () => ({
  SidebarBase: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ICON_SIZE_DEFAULT: 16,
}));
vi.mock('@/config', async () => ({
  ChatSidebarFooter: () => null,
  chatSlots: (await vi.importActual<typeof import('@/app/(main)/chat/chat-extension')>('@/app/(main)/chat/chat-extension'))
    .defaultChatSlots,
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/chat/utils/current-user-id', () => ({ getCurrentUserId: () => 'user-a' }));
vi.mock('@/lib/store/mobile-sidebar-store', () => ({
  useMobileSidebarStore: (selector: (s: { close: () => void; isOpen: boolean }) => unknown) =>
    selector({ close: vi.fn(), isOpen: false }),
}));
vi.mock('@/app/(main)/agents/api', () => ({
  AgentsApi: { getAgent: h.getAgent, fetchAgentConversations: h.fetchAgentConversations },
  agentDetailKey: (id: string) => ['agents/detail', id] as const,
}));
vi.mock('@/chat/build-chat-url', () => ({ openFreshAgentChat: vi.fn() }));
vi.mock('../agent-sidebar-row-access', () => ({ getAgentSidebarRowMenuAccess: () => ({}) }));
vi.mock('../header', () => ({ ChatSidebarHeader: () => null }));
vi.mock('../sidebar-item', () => ({
  SidebarItem: ({ label, href }: { label: React.ReactNode; href?: string }) =>
    href ? <a href={href}>{label}</a> : <span>{label}</span>,
}));
vi.mock('../agent-more-chats-sidebar', () => ({ AgentMoreChatsSidebar: () => null }));
vi.mock('../chat-section', () => ({
  ChatSection: ({
    title,
    conversations,
    timeGroups,
    agentId,
    emptyStateText,
    variant,
    onAdd,
    isLoading,
  }: {
    title?: string;
    emptyStateText?: string;
    isLoading?: boolean;
    conversations?: Array<{ id: string; title: string }>;
    timeGroups?: Array<[string, Array<{ id: string; title: string }>]>;
    agentId?: string;
    variant?: string;
    onAdd?: () => void;
  }) => {
    const rows = conversations ?? (timeGroups ?? []).flatMap(([, list]) => list);
    return (
      <section
        data-testid={title ?? 'untitled'}
        data-agent={agentId}
        data-variant={variant}
        data-grouped={timeGroups ? 'time' : 'flat'}
        data-days={timeGroups?.map(([day, list]) => `${day}: ${list.map((c) => c.id).join(' ')}`).join(' | ')}
        data-add={onAdd ? 'yes' : 'no'}
        data-loading={isLoading ? 'yes' : 'no'}
      >
        {rows.map((c) => (
          <span key={c.id}>{c.title}</span>
        ))}
        {rows.length === 0 && emptyStateText && <em>{emptyStateText}</em>}
      </section>
    );
  },
}));

import { clearSessionCache } from '@/lib/api/session-cache';
import { useChatStore } from '@/chat/store';
import { AgentScopedChatSidebar } from '../agent-scoped-chat-sidebar';

const conv = (id: string, title: string, isOwner: boolean) =>
  ({ id, title, isOwner, createdAt: 1, updatedAt: 1, isShared: !isOwner }) as never;

const pagination = { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false };

const renderSidebar = () =>
  render(
    <Theme>
      <AgentScopedChatSidebar agentId="agent-7" />
    </Theme>,
  );

beforeEach(() => {
  // The agent detail is cached for 60 s; each case starts from an empty cache.
  clearSessionCache();
  // Each test starts as a first visit: nothing of the agent's is listed yet.
  useChatStore.getState().reset();
  h.getAgent.mockResolvedValue({ agent: { _key: 'agent-7' } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('agent sidebar: chats shared with the user', () => {
  it('lists chats other people shared under "Shared Chats", apart from the user’s own', async () => {
    h.fetchAgentConversations.mockResolvedValue({
      conversations: [conv('mine-1', 'My roadmap chat', true)],
      sharedConversations: [conv('shared-1', 'Pranjal’s Q3 review', false)],
      pagination,
    });

    renderSidebar();

    await waitFor(() => expect(screen.getByTestId('chat.sharedChats').textContent).toBe('Pranjal’s Q3 review'));
    expect(screen.getByTestId('chat.yourChats').textContent).toBe('My roadmap chat');
    expect(screen.getByTestId('chat.sharedChats').getAttribute('data-agent')).toBe('agent-7');
  });

  it('shows no "Shared Chats" section when nothing was shared', async () => {
    h.fetchAgentConversations.mockResolvedValue({
      conversations: [conv('mine-1', 'My roadmap chat', true)],
      sharedConversations: [],
      pagination,
    });

    renderSidebar();

    await waitFor(() => expect(screen.getByText('My roadmap chat')).toBeTruthy());
    expect(screen.queryByTestId('chat.sharedChats')).toBeNull();
  });

  it('still lists the chats shared with the user when they have no access to the agent itself', async () => {
    h.getAgent.mockRejectedValue(new Error('403'));
    h.fetchAgentConversations.mockResolvedValue({
      conversations: [],
      sharedConversations: [conv('shared-1', 'Pranjal’s Q3 review', false)],
      pagination,
    });

    renderSidebar();

    await waitFor(() => expect(screen.getByTestId('chat.sharedChats').textContent).toBe('Pranjal’s Q3 review'));
  });

  it('drops the shared chats when the list fails to load', async () => {
    h.fetchAgentConversations.mockRejectedValue(new Error('boom'));

    renderSidebar();

    await waitFor(() => expect(h.fetchAgentConversations).toHaveBeenCalled());
    expect(screen.queryByTestId('chat.sharedChats')).toBeNull();
  });

  it('offers New Chat when the user can open the agent', async () => {
    h.fetchAgentConversations.mockResolvedValue({ conversations: [], sharedConversations: [], pagination });

    renderSidebar();

    await waitFor(() => expect(h.fetchAgentConversations).toHaveBeenCalled());
    expect(await screen.findByText('chat.newChat')).toBeTruthy();
  });

  it('does not offer New Chat, or a start-a-chat prompt, when the user cannot open the agent', async () => {
    h.getAgent.mockRejectedValue(new Error('404'));
    h.fetchAgentConversations.mockResolvedValue({
      conversations: [],
      sharedConversations: [conv('shared-1', 'Pranjal’s Q3 review', false)],
      pagination,
    });

    renderSidebar();

    await waitFor(() => expect(screen.getByTestId('chat.sharedChats')).toBeTruthy());
    await waitFor(() => expect(screen.queryByText('chat.newChat')).toBeNull());
    expect(screen.getByText('chat.noChatsYet')).toBeTruthy();
    expect(screen.getByTestId('chat.yourChats').getAttribute('data-add')).toBe('no');
  });
});

describe('agent sidebar: the way back and the sections', () => {
  it('leads back to All agents, the Agents page, instead of All chats', async () => {
    h.fetchAgentConversations.mockResolvedValue({ conversations: [], sharedConversations: [], pagination });

    renderSidebar();

    const back = await screen.findByRole('link', { name: 'chat.backToAgents' });
    expect(back.getAttribute('href')).toBe('/agents/');
    expect(screen.queryByText('chat.backToChatHome')).toBeNull();
  });

  it("lists the agent's chats in the main rail's sections: titled, Shared chats flat and Your chats by day, with no Recents toggle", async () => {
    h.fetchAgentConversations.mockResolvedValue({
      conversations: [conv('mine-1', 'My roadmap chat', true)],
      sharedConversations: [conv('shared-1', 'Pranjal’s Q3 review', false)],
      pagination,
    });

    renderSidebar();

    const yours = await screen.findByTestId('chat.yourChats');
    await waitFor(() => expect(yours.textContent).toBe('My roadmap chat'));
    const shared = screen.getByTestId('chat.sharedChats');
    for (const section of [yours, shared]) {
      expect(section.getAttribute('data-variant')).toBe('rail');
    }
    expect(shared.getAttribute('data-grouped')).toBe('flat');
    expect(yours.getAttribute('data-grouped')).toBe('time');
    // Your chats has the rail's +, which starts a chat with this agent.
    expect(yours.getAttribute('data-add')).toBe('yes');
    expect(screen.queryByText('chat.recents')).toBeNull();
  });

  it('titles Your chats even when nothing was shared, and says what will fill it when empty', async () => {
    h.fetchAgentConversations.mockResolvedValue({ conversations: [], sharedConversations: [], pagination });

    renderSidebar();

    const yours = await screen.findByTestId('chat.yourChats');
    await waitFor(() => expect(yours.textContent).toBe('chat.yourChatsEmpty'));
    expect(screen.queryByTestId('untitled')).toBeNull();
  });
});

describe('agent sidebar: coming back keeps what it listed', () => {
  const listed = {
    conversations: [conv('mine-1', 'My roadmap chat', true)],
    sharedConversations: [conv('shared-1', 'Pranjal’s Q3 review', false)],
    pagination,
  };

  it('the same agent again shows its chats at once, with no skeleton, while they load again', async () => {
    h.fetchAgentConversations.mockResolvedValue(listed);
    const first = renderSidebar();
    await waitFor(() => expect(screen.getByTestId('chat.yourChats').textContent).toBe('My roadmap chat'));
    first.unmount();
    h.fetchAgentConversations.mockReturnValue(new Promise(() => {}));

    renderSidebar();

    expect(screen.getByTestId('chat.yourChats').textContent).toBe('My roadmap chat');
    expect(screen.getByTestId('chat.sharedChats').textContent).toBe('Pranjal’s Q3 review');
    expect(screen.getByTestId('chat.yourChats').getAttribute('data-loading')).toBe('no');
    await waitFor(() => expect(h.fetchAgentConversations).toHaveBeenCalledTimes(2));
  });

  it('a refresh that fails keeps the rows on screen', async () => {
    h.fetchAgentConversations.mockResolvedValue(listed);
    const first = renderSidebar();
    await waitFor(() => expect(screen.getByTestId('chat.yourChats').textContent).toBe('My roadmap chat'));
    first.unmount();
    h.fetchAgentConversations.mockRejectedValue(new Error('boom'));

    renderSidebar();

    await waitFor(() => expect(h.fetchAgentConversations).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(useChatStore.getState().agentConversationsAgentId).toBe('agent-7'));
    expect(screen.getByTestId('chat.yourChats').textContent).toBe('My roadmap chat');
    expect(screen.getByTestId('chat.sharedChats').textContent).toBe('Pranjal’s Q3 review');
    expect(useChatStore.getState().agentConversationsError).toBeNull();
  });

  it("never shows another agent's chats, even while the store still holds them", () => {
    // The rail already on this agent, so nothing clears the lists before the first frame paints.
    useChatStore.setState({
      agentSidebarAgentId: 'agent-7',
      agentConversations: [conv('other-1', 'Another agent chat', true)],
      agentSharedConversations: [conv('other-2', 'Another shared chat', false)],
      agentConversationsAgentId: 'agent-other',
    });
    h.fetchAgentConversations.mockReturnValue(new Promise(() => {}));

    renderSidebar();

    expect(screen.queryByText('Another agent chat')).toBeNull();
    expect(screen.queryByText('Another shared chat')).toBeNull();
  });

  it("a load that lands after the rail left the agent (a sign-out, another page) is dropped, not kept for the next", async () => {
    let answer: (value: unknown) => void = () => {};
    h.fetchAgentConversations.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const first = renderSidebar();
    await waitFor(() => expect(h.fetchAgentConversations).toHaveBeenCalledTimes(1));
    first.unmount();
    useChatStore.getState().clearSidebarLists();

    await act(async () => {
      answer({ conversations: [conv('a-1', 'Last person agent chat', true)], sharedConversations: [], pagination });
    });

    expect(useChatStore.getState().agentConversations).toEqual([]);
    h.fetchAgentConversations.mockReturnValue(new Promise(() => {}));
    renderSidebar();
    expect(screen.queryByText('Last person agent chat')).toBeNull();
  });

  it("a late answer for the agent left behind never touches the next agent's rail", async () => {
    let answerA: (value: unknown) => void = () => {};
    h.fetchAgentConversations.mockReturnValueOnce(new Promise((resolve) => (answerA = resolve)));
    const view = renderSidebar();
    await waitFor(() => expect(h.fetchAgentConversations).toHaveBeenCalledTimes(1));
    h.fetchAgentConversations.mockResolvedValueOnce({ conversations: [conv('b-1', 'Agent B chat', true)], sharedConversations: [], pagination });
    view.rerender(
      <Theme>
        <AgentScopedChatSidebar agentId="agent-8" />
      </Theme>,
    );
    await waitFor(() => expect(screen.queryByText('Agent B chat')).not.toBeNull());

    await act(async () => {
      answerA({ conversations: [conv('a-1', 'Agent A chat', true)], sharedConversations: [], pagination });
    });

    expect(screen.queryByText('Agent A chat')).toBeNull();
    expect(screen.getByText('Agent B chat')).toBeTruthy();
  });

  it('the window coming back into focus reloads the chats once they are stale, not before', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ['Date'] });
    try {
      h.fetchAgentConversations.mockResolvedValue(listed);
      renderSidebar();
      await waitFor(() => expect(screen.getByTestId('chat.yourChats').textContent).toBe('My roadmap chat'));

      act(() => {
        window.dispatchEvent(new Event('focus'));
      });
      expect(h.fetchAgentConversations).toHaveBeenCalledTimes(1);

      vi.setSystemTime(Date.now() + 31_000);
      act(() => {
        window.dispatchEvent(new Event('focus'));
      });
      await waitFor(() => expect(h.fetchAgentConversations).toHaveBeenCalledTimes(2));
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('agent sidebar: Your chats by day', () => {
  // Midday, so no fixture crosses local midnight.
  const NOW = new Date(2026, 0, 15, 12, 0, 0).getTime();
  const HOUR = 60 * 60 * 1000;
  const DAY = 24 * HOUR;
  const dated = (id: string, time: number, isOwner = true) =>
    ({ id, title: id, isOwner, isShared: !isOwner, createdAt: '', updatedAt: '', lastActivityAt: time }) as never;
  const days = () => screen.getByTestId('chat.yourChats').getAttribute('data-days');

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it('files the rows under Today, Yesterday, Last 7 days and Older, in that order, with only the days that have one', async () => {
    h.fetchAgentConversations.mockResolvedValue({
      conversations: [dated('old', NOW - 30 * DAY), dated('today', NOW - HOUR), dated('week', NOW - 3 * DAY), dated('yday', NOW - DAY)],
      sharedConversations: [dated('s1', NOW - 3 * DAY, false)],
      pagination,
    });
    renderSidebar();

    await waitFor(() =>
      expect(days()).toBe('Today: today | Yesterday: yday | Previous 7 Days: week | Older: old'),
    );
    expect(screen.getByTestId('chat.sharedChats').getAttribute('data-grouped')).toBe('flat');
    cleanup();

    h.fetchAgentConversations.mockResolvedValue({
      conversations: [dated('yday', NOW - DAY), dated('old', NOW - 9 * DAY)],
      sharedConversations: [],
      pagination,
    });
    useChatStore.getState().reset();
    renderSidebar();
    await waitFor(() => expect(days()).toBe('Yesterday: yday | Older: old'));
  });

  it('a new message puts the chat under Today at once, first', async () => {
    h.fetchAgentConversations.mockResolvedValue({
      conversations: [dated('t1', NOW - 2 * HOUR), dated('y1', NOW - DAY)],
      sharedConversations: [],
      pagination,
    });
    renderSidebar();
    await waitFor(() => expect(days()).toBe('Today: t1 | Yesterday: y1'));

    // What the end of an answer does: the chat moves up with the time the server saved for it.
    act(() => useChatStore.getState().moveConversationToTop('y1', { lastActivityAt: NOW }));
    expect(days()).toBe('Today: y1 t1');
  });

  it('past midnight, the rows move on a day when the rail is next seen, with no reload', async () => {
    const lateEvening = new Date(2026, 0, 15, 23, 59, 30).getTime();
    vi.setSystemTime(lateEvening);
    h.fetchAgentConversations.mockResolvedValue({
      conversations: [dated('evening', lateEvening - HOUR), dated('monday', lateEvening - 2 * DAY)],
      sharedConversations: [],
      pagination,
    });
    renderSidebar();
    await waitFor(() => expect(days()).toBe('Today: evening | Previous 7 Days: monday'));

    vi.setSystemTime(lateEvening + 60_000);
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(days()).toBe('Yesterday: evening | Previous 7 Days: monday');
  });

  it('offers New Chat while the agent is still loading', async () => {
    h.getAgent.mockReturnValue(new Promise(() => {}));
    h.fetchAgentConversations.mockResolvedValue({ conversations: [], sharedConversations: [], pagination });

    renderSidebar();

    expect(await screen.findByText('chat.newChat')).toBeTruthy();
  });
});

describe('agent sidebar: the agent detail', () => {
  it('asks for the agent once, and a chats refresh does not ask again', async () => {
    h.fetchAgentConversations.mockResolvedValue({ conversations: [], sharedConversations: [], pagination });

    renderSidebar();
    await waitFor(() => expect(h.fetchAgentConversations).toHaveBeenCalledTimes(1));
    expect(h.getAgent).toHaveBeenCalledTimes(1);
    expect(h.getAgent).toHaveBeenCalledWith('agent-7', { quiet: true });

    act(() => useChatStore.getState().bumpConversationsVersion());

    await waitFor(() => expect(h.fetchAgentConversations).toHaveBeenCalledTimes(2));
    expect(h.getAgent).toHaveBeenCalledTimes(1);
  });

  it('reuses the agent for a remount within 60 s', async () => {
    h.fetchAgentConversations.mockResolvedValue({ conversations: [], sharedConversations: [], pagination });

    renderSidebar();
    await waitFor(() => expect(h.getAgent).toHaveBeenCalledTimes(1));
    cleanup();
    renderSidebar();

    await waitFor(() => expect(h.fetchAgentConversations).toHaveBeenCalledTimes(2));
    expect(h.getAgent).toHaveBeenCalledTimes(1);
  });
});
