import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
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

// The open menu measures itself; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const renderHeader = (props: Partial<React.ComponentProps<typeof AgentChatHeader>> & { displayName: string | null }) =>
  render(
    <Theme>
      <AgentChatHeader agentId="agent-7" isMobile={false} {...props} />
    </Theme>,
  );

function openMenu() {
  fireEvent.keyDown(screen.getByLabelText('chat.agentHeaderMenuAria'), { key: 'Enter' });
}

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

  it('lists the agent creator in the menu on a phone', () => {
    renderHeader({ displayName: 'Support Bot', isMobile: true, creatorName: 'Ada' });
    openMenu();
    expect(screen.getByText('agentBuilder.createdBy: Ada')).toBeTruthy();
  });

  it('keeps the creator out of the menu on desktop, where the chip shows it', () => {
    renderHeader({ displayName: 'Support Bot', creatorName: 'Ada' });
    openMenu();
    expect(screen.queryByText('agentBuilder.createdBy: Ada')).toBeNull();
  });

  it('stops the mobile title short of the share group', () => {
    const { container } = renderHeader({ displayName: 'Support Bot', isMobile: true, hasShareGroup: true });
    expect((container.querySelector('[style*="absolute"]') as HTMLElement).style.maxWidth).toBe(
      'calc(100vw - 52px - 200px)',
    );
  });
});
