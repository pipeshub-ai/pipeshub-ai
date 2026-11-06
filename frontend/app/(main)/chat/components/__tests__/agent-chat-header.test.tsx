import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const router = vi.hoisted(() => ({ push: vi.fn() }));
const drawer = vi.hoisted(() => ({ close: vi.fn(), open: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en-US' } }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: ({ name }: { name: string }) => <span data-icon={name} /> }));
vi.mock('@/lib/store/mobile-sidebar-store', () => ({
  useMobileSidebarStore: (selector: (s: { close: () => void; open: () => void }) => unknown) => selector(drawer),
}));
vi.mock('@/chat/build-chat-url', () => ({ openFreshAgentChat: vi.fn() }));
vi.mock('@/chat/store', () => {
  const state = {
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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

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

  it.each([[true], [false]])('names no creator in the menu, on a phone or a desktop (isMobile %s)', (isMobile) => {
    renderHeader({ displayName: 'Support Bot', isMobile, creatorName: 'Ada' } as never);
    openMenu();
    expect(screen.queryByText(/createdBy|Ada/)).toBeNull();
  });

  it('leads New chat, All chats and More agents with their glyphs, as every other item in it', () => {
    renderHeader({ displayName: 'Support Bot' });
    openMenu();
    expect(screen.getAllByRole('menuitem').map((el) => [el.textContent, el.querySelector('[data-icon]')?.getAttribute('data-icon')])).toEqual([
      ['chat.agentMenuNewChat', 'assistant'],
      ['chat.agentMenuAllChats', 'forum'],
      ['chat.moreAgents', 'smart_toy'],
    ]);
  });

  it('opens the Agents page from More agents', () => {
    renderHeader({ displayName: 'Support Bot' });
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'chat.moreAgents' }));
    expect(router.push).toHaveBeenCalledWith('/agents/');
    expect(drawer.open).not.toHaveBeenCalled();
  });

  it('closes the drawer on a phone, and never opens it, on the way to the Agents page', () => {
    renderHeader({ displayName: 'Support Bot', isMobile: true });
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'chat.moreAgents' }));
    expect(drawer.close).toHaveBeenCalled();
    expect(drawer.open).not.toHaveBeenCalled();
    expect(router.push).toHaveBeenCalledWith('/agents/');
  });

  it('stops the mobile title short of the share group', () => {
    const { container } = renderHeader({ displayName: 'Support Bot', isMobile: true, hasShareGroup: true });
    expect((container.querySelector('[style*="absolute"]') as HTMLElement).style.maxWidth).toBe(
      'calc(100vw - 52px - 200px)',
    );
  });
});
