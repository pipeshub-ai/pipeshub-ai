import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en-US' } }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/store/mobile-sidebar-store', () => ({
  useMobileSidebarStore: (selector: (s: { close: () => void; open: () => void }) => unknown) =>
    selector({ close: vi.fn(), open: vi.fn() }),
}));
vi.mock('@/chat/build-chat-url', () => ({ openFreshAgentChat: vi.fn() }));
vi.mock('@/chat/store', () => {
  const state = {
    toggleAgentsSidebar: vi.fn(),
    closeAgentsSidebar: vi.fn(),
    agentContextAccess: null,
  };
  return { useChatStore: (selector: (s: typeof state) => unknown) => selector(state) };
});

import { AgentChatHeader } from '../agent-chat-header';

const renderHeader = (props: { displayName: string | null; readOnly?: boolean }) =>
  render(
    <Theme>
      <AgentChatHeader agentId="agent-7" isMobile={false} {...props} />
    </Theme>,
  );

afterEach(cleanup);

describe('agent chat header', () => {
  it('shows the agent name with its menu', () => {
    renderHeader({ displayName: 'Support Bot' });
    expect(screen.getByText('Support Bot')).toBeTruthy();
    expect(screen.getByLabelText('chat.agentHeaderMenuAria')).toBeTruthy();
  });

  it('says it is loading until the name arrives', () => {
    renderHeader({ displayName: null });
    expect(screen.getByText('chat.agentNameLoading')).toBeTruthy();
    expect(screen.getByLabelText('chat.agentHeaderMenuAria')).toBeTruthy();
  });

  it('titles a shared chat whose agent the user cannot open, with no menu and no endless loading', () => {
    renderHeader({ displayName: null, readOnly: true });
    expect(screen.getByText('chat.sharedAgentChat')).toBeTruthy();
    expect(screen.queryByText('chat.agentNameLoading')).toBeNull();
    expect(screen.queryByLabelText('chat.agentHeaderMenuAria')).toBeNull();
  });

  it('keeps the name and menu on a shared chat when the agent did load', () => {
    renderHeader({ displayName: 'Support Bot', readOnly: true });
    expect(screen.getByText('Support Bot')).toBeTruthy();
    expect(screen.getByLabelText('chat.agentHeaderMenuAria')).toBeTruthy();
  });
});
