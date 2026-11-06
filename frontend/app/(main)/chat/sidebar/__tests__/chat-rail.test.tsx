/**
 * One chat rail for the main, agent and project views: its header and footer stay mounted (the org is not fetched
 * again, the focus stays put) and only the middle swaps with the URL. The main lists load only where they show.
 */
import React, { useEffect } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { create } from 'zustand';

const nav = create<{ params: URLSearchParams; pathname: string }>(() => ({
  params: new URLSearchParams(),
  pathname: '/chat/',
}));
const h = vi.hoisted(() => ({
  mounts: { header: 0, footer: 0, railEffects: 0 },
  unmounts: { railEffects: 0 },
  listsEnabled: [] as boolean[],
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => nav((s) => s.params),
  usePathname: () => nav((s) => s.pathname),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/app/components/sidebar', () => ({
  SidebarBase: ({
    header,
    pinned,
    children,
    footer,
    secondaryPanel,
  }: Record<'header' | 'pinned' | 'children' | 'footer' | 'secondaryPanel', React.ReactNode>) => (
    <div>
      {header}
      {pinned}
      <main>{children}</main>
      {footer}
      {secondaryPanel && <aside>{secondaryPanel}</aside>}
    </div>
  ),
}));
vi.mock('@/config', () => ({
  ChatSidebarFooter: () => {
    useEffect(() => {
      h.mounts.footer += 1;
    }, []);
    return <footer>footer</footer>;
  },
  chatSlots: {
    useRailEffects: () => {
      useEffect(() => {
        h.mounts.railEffects += 1;
        return () => {
          h.unmounts.railEffects += 1;
        };
      }, []);
    },
  },
}));
vi.mock('../header', () => ({
  ChatSidebarHeader: () => {
    useEffect(() => {
      h.mounts.header += 1;
    }, []);
    return <header>header</header>;
  },
}));
vi.mock('../static-nav-section', () => ({ StaticNavSection: () => <nav>nav rows</nav> }));
vi.mock('../chat-sections', () => ({ ChatSections: () => <div>main chats</div> }));
vi.mock('../more-chats-sidebar', () => ({ MoreChatsSidebar: () => <div>more chats</div> }));
vi.mock('../agent-scoped-chat-sidebar', () => ({
  AgentScopedChatSidebar: ({ agentId }: { agentId: string }) => <div>agent chats {agentId}</div>,
}));
vi.mock('../agent-more-chats-sidebar', () => ({ AgentMoreChatsSidebar: () => <div>agent more chats</div> }));
vi.mock('../project-conversations-sidebar', () => ({
  ProjectConversationsRail: ({ projectId }: { projectId: string }) => <div>project chats {projectId}</div>,
}));
vi.mock('../use-sidebar-conversations', () => ({
  useSidebarConversations: (enabled: boolean) => {
    h.listsEnabled.push(enabled);
  },
}));

const { useChatStore } = await import('@/chat/store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { default: ChatSidebar } = await import('..');

const go = (query: string, pathname = '/chat/') => act(() => nav.setState({ params: new URLSearchParams(query), pathname }));

beforeEach(() => {
  h.mounts = { header: 0, footer: 0, railEffects: 0 };
  h.unmounts = { railEffects: 0 };
  h.listsEnabled = [];
  useChatStore.getState().reset();
  useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as never);
  nav.setState({ params: new URLSearchParams(), pathname: '/chat/' });
});
afterEach(() => cleanup());

describe('the chat rail across its views', () => {
  it('swaps only the middle: main, an agent, a project and back, with one header, one footer and one set of watches', () => {
    render(<ChatSidebar />);
    expect(screen.getByText('main chats')).toBeTruthy();
    expect(screen.getByText('nav rows')).toBeTruthy();

    go('agentId=agent-1');
    expect(screen.getByText('agent chats agent-1')).toBeTruthy();
    expect(screen.queryByText('nav rows')).toBeNull();

    go('projectId=p1');
    expect(screen.getByText('project chats p1')).toBeTruthy();

    go('', '/agents/');
    expect(screen.getByText('main chats')).toBeTruthy();

    expect(h.mounts).toEqual({ header: 1, footer: 1, railEffects: 1 });
  });

  it('loads the main lists only while they show', () => {
    render(<ChatSidebar />);
    go('agentId=agent-1');
    go('projectId=p1');
    go('');

    expect(h.listsEnabled[0]).toBe(true);
    expect(h.listsEnabled).toContain(false);
    expect(h.listsEnabled[h.listsEnabled.length - 1]).toBe(true);
  });

  it("shows the main More beside the main chats only, and an agent's More beside its chats", () => {
    act(() => useChatStore.getState().toggleMoreChatsPanel('your'));
    render(<ChatSidebar />);
    expect(screen.getByText('more chats')).toBeTruthy();

    go('agentId=agent-1');
    expect(screen.queryByText('more chats')).toBeNull();
    act(() => useChatStore.getState().toggleAgentMoreChatsPanel());
    expect(screen.getByText('agent more chats')).toBeTruthy();
  });

  it('More stays open from page to page, and closes with the rail (a page with a rail of its own)', () => {
    act(() => useChatStore.getState().toggleMoreChatsPanel('your'));
    const rail = render(<ChatSidebar />);
    go('', '/agents/');
    go('', '/artifacts/');
    expect(screen.getByText('more chats')).toBeTruthy();
    expect(useChatStore.getState().isMoreChatsPanelOpen).toBe(true);

    rail.unmount();

    expect(useChatStore.getState().isMoreChatsPanelOpen).toBe(false);
    expect(h.unmounts.railEffects).toBe(1);
  });
});
