import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, render, screen, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useChatStore } from '@/chat/store';
import type { Conversation } from '@/chat/types';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/navigation', () => ({
  Link: ({ href, children, ...rest }: React.ComponentProps<'a'>) => <a href={href as string} {...rest}>{children}</a>,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/chat/collaboration-api', async (orig) => ({
  ...(await orig<typeof import('@/chat/collaboration-api')>()),
  CollaborationApi: {},
}));
const chatApi = vi.hoisted(() => ({ fetchConversations: vi.fn() }));
vi.mock('@/chat/api', () => ({ ChatApi: chatApi }));
const agentsApi = vi.hoisted(() => ({ fetchAgentConversations: vi.fn() }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: agentsApi }));
vi.mock('@/chat/project-api', () => ({ ProjectApi: {} }));

import { MoreChatsSidebar } from '../more-chats-sidebar';
import { AgentMoreChatsSidebar } from '../agent-more-chats-sidebar';

const PAGINATION = { page: 1, limit: 20, totalCount: 2, totalPages: 1, hasNextPage: false, hasPrevPage: false };

function row(id: string, over: Partial<Conversation> = {}): Conversation {
  return {
    id,
    title: `Chat ${id}`,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    isShared: true,
    sharedWith: [],
    isOwner: false,
    access: {
      role: 'read', isOwner: false, accessLevel: 'read', canSend: false, canManage: false, canInvite: false,
      isCollaborative: true,
    },
    unreadCount: 2,
    ...over,
  };
}

function setFlag(on: boolean) {
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: on } } as never);
}

function loseAccess(convId: string) {
  act(() => {
    const store = useChatStore.getState();
    const slotId = store.createSlot(convId);
    store.updateSlot(slotId, { accessLost: true });
  });
}

const PANELS = [
  {
    name: 'MoreChatsSidebar',
    mock: () => chatApi.fetchConversations,
    element: () => <MoreChatsSidebar sectionType="shared" onBack={() => {}} />,
  },
  {
    name: 'AgentMoreChatsSidebar',
    mock: () => agentsApi.fetchAgentConversations,
    element: () => <AgentMoreChatsSidebar agentId="a1" onBack={() => {}} />,
  },
];

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} });
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} unobserve() {} });
  vi.clearAllMocks();
  useChatStore.getState().reset();
  setFlag(true);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe.each(PANELS)('$name (09e badges and access-lost)', ({ mock, element }) => {
  beforeEach(() => {
    const result = { conversations: [row('c1'), row('c2')], sharedConversations: [], pagination: PAGINATION };
    mock().mockResolvedValue(result);
  });

  it('shows the role badge and unread dot on each row', async () => {
    render(<Theme>{element()}</Theme>);
    expect(await screen.findByText('Chat c1')).toBeTruthy();
    expect(screen.getAllByRole('img', { name: 'Can view' })).toHaveLength(2);
    expect(screen.getAllByRole('img', { name: '2 unread messages' })).toHaveLength(2);
  });

  it('drops a chat whose open slot lost access, now and when it happens later', async () => {
    loseAccess('c1');
    render(<Theme>{element()}</Theme>);
    expect(await screen.findByText('Chat c2')).toBeTruthy();
    expect(screen.queryByText('Chat c1')).toBeNull();
    cleanup();

    useChatStore.getState().reset();
    render(<Theme>{element()}</Theme>);
    expect(await screen.findByText('Chat c1')).toBeTruthy();
    loseAccess('c1');
    expect(screen.queryByText('Chat c1')).toBeNull();
    expect(screen.getByText('Chat c2')).toBeTruthy();
  });

  it('flag off: no badges, and a lost-access slot does not hide the row', async () => {
    setFlag(false);
    loseAccess('c1');
    render(<Theme>{element()}</Theme>);
    expect(await screen.findByText('Chat c1')).toBeTruthy();
    expect(screen.queryByText('Can view')).toBeNull();
    expect(screen.queryByRole('img')).toBeNull();
  });
});
