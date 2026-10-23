import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
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
vi.mock('@/config', () => ({ ChatSidebarFooter: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/store/mobile-sidebar-store', () => ({
  useMobileSidebarStore: (selector: (s: { close: () => void; isOpen: boolean }) => unknown) =>
    selector({ close: vi.fn(), isOpen: false }),
}));
vi.mock('@/app/(main)/agents/api', () => ({
  AgentsApi: { getAgent: h.getAgent, fetchAgentConversations: h.fetchAgentConversations },
}));
vi.mock('@/chat/build-chat-url', () => ({ openFreshAgentChat: vi.fn() }));
vi.mock('../agent-sidebar-row-access', () => ({ getAgentSidebarRowMenuAccess: () => ({}) }));
vi.mock('../header', () => ({ ChatSidebarHeader: () => null }));
vi.mock('../sidebar-item', () => ({ SidebarItem: ({ label }: { label: React.ReactNode }) => <span>{label}</span> }));
vi.mock('../agent-more-chats-sidebar', () => ({ AgentMoreChatsSidebar: () => null }));
vi.mock('../agents-sidebar', () => ({ AgentsSidebar: () => null }));
vi.mock('../chat-section', () => ({
  ChatSection: ({
    title,
    conversations,
    timeGroups,
    agentId,
    emptyStateText,
  }: {
    title?: string;
    emptyStateText?: string;
    conversations?: Array<{ id: string; title: string }>;
    timeGroups?: Array<[string, Array<{ id: string; title: string }>]>;
    agentId?: string;
  }) => {
    const rows = conversations ?? (timeGroups ?? []).flatMap(([, list]) => list);
    return (
      <section data-testid={title ?? 'recents'} data-agent={agentId}>
        {rows.map((c) => (
          <span key={c.id}>{c.title}</span>
        ))}
        {rows.length === 0 && emptyStateText && <em>{emptyStateText}</em>}
      </section>
    );
  },
}));

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
    expect(screen.queryByText('chat.newChat')).toBeNull();
    expect(screen.getByText('chat.noChatsYet')).toBeTruthy();
  });
});
