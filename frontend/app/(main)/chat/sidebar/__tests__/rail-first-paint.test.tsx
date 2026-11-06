/**
 * The rail's first paint keeps the room of what the last session showed, so nothing under it moves when the lists
 * and the flags arrive: the Projects row while the flags load, and Pinned and Shared chats while the first lists load.
 * A remembered boolean or row count each, per person on the device, never a title.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { getI18n } from 'react-i18next';
import type { Conversation } from '@/chat/types';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/chat/utils/current-user-id', () => ({ getCurrentUserId: () => 'user-1' }));
vi.mock('../chat-section-element', () => ({
  ChatSectionElement: ({ conversation }: { conversation: Conversation }) => <div data-testid="row">{conversation.title}</div>,
  GeneratingTitleItem: () => null,
  StartChatButton: () => null,
  ChatItemSkeleton: () => null,
}));

import { useChatStore } from '@/chat/store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { ChatSections } from '../chat-sections';
import { ChatSection } from '../chat-section';
import { StaticNavSection } from '../static-nav-section';

// The section's name comes from the translations, which an edition may word differently.
const SHARED = () => getI18n().t('chat.sharedChats');

const chat = (id: string) =>
  ({ id, title: `Chat ${id}`, createdAt: '', updatedAt: '', isShared: false, sharedWith: [], lastActivityAt: 1 }) as unknown as Conversation;

beforeEach(() => {
  window.localStorage.clear();
  useChatStore.getState().reset();
});
afterEach(() => cleanup());

const DAY_MS = 24 * 60 * 60 * 1000;
/** What the last session left on this device for user-1, `ageMs` ago. */
const remember = (name: string, v: number, ageMs = 0) =>
  window.localStorage.setItem(`pipeshub.rail.user-1.${name}`, JSON.stringify({ v, at: Date.now() - ageMs }));
const stored = (name: string) => {
  const value = window.localStorage.getItem(`pipeshub.rail.user-1.${name}`);
  return value === null ? null : (JSON.parse(value) as { v: number; at: number });
};
const skeletonRows = () =>
  [...document.querySelectorAll('[data-list-skeleton]')].map((s) => s.querySelector('[aria-hidden="true"]')!.children.length);
const renderSections = () => render(<ChatSections onOpenMoreChats={() => {}} />);
const pin = (id: string) => ({ ...chat(id), isPinned: true }) as Conversation;

describe('the Projects row while the flags load', () => {
  const projectsRow = () => screen.queryByText('Projects');
  const renderNav = () =>
    render(
      <Theme>
        <StaticNavSection />
      </Theme>,
    );

  it('shows as it did last time: there before the flags when Projects was on', () => {
    remember('projects', 1);
    useFeatureFlagsStore.setState({ flags: null } as never);

    renderNav();

    expect(projectsRow()).not.toBeNull();
  });

  it('with nothing remembered, waits for the flags, then remembers what they said', () => {
    useFeatureFlagsStore.setState({ flags: null } as never);
    renderNav();
    expect(projectsRow()).toBeNull();

    act(() => useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as never));

    expect(projectsRow()).not.toBeNull();
    expect(stored('projects')?.v).toBe(1);
  });

  it('the flags win over what was remembered', () => {
    remember('projects', 1);
    useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: false } } as never);

    renderNav();

    expect(projectsRow()).toBeNull();
    expect(stored('projects')?.v).toBe(0);
  });
});

describe('Shared chats on the first load', () => {
  it('keeps the rows it showed last time, its More included, from the first frame', () => {
    remember('sharedRows', 6);

    renderSections();

    expect(screen.getByText(SHARED())).toBeTruthy();
    expect(skeletonRows()).toEqual([6]);
  });

  it('one shared chat last time keeps the room of one row, not of a full section', () => {
    remember('sharedRows', 1);

    renderSections();

    expect(skeletonRows()).toEqual([1]);
  });

  it('keeps no room when the last session had none, or nothing was remembered', () => {
    remember('sharedRows', 0);
    renderSections();
    expect(screen.queryByText(SHARED())).toBeNull();
  });

  it('once the lists are in, shows only what they hold', () => {
    remember('sharedRows', 6);
    renderSections();

    act(() => useChatStore.setState({ conversations: [chat('a')], sharedConversations: [], conversationsLoadedAt: Date.now() }));

    expect(screen.queryByText(SHARED())).toBeNull();
  });
});

describe('Pinned on the first load', () => {
  it('keeps the rows it showed last time, its More included, from the first frame', () => {
    remember('pinnedRows', 3);

    renderSections();

    expect(screen.getByText('Pinned chats')).toBeTruthy();
    // Pinned's three. Your chats is under it, so its own skeleton, which comes with the load, moves nothing.
    expect(skeletonRows()).toEqual([3]);
  });

  it('keeps no room when nothing was pinned last time, or nothing was remembered', () => {
    remember('pinnedRows', 0);
    renderSections();
    expect(screen.queryByText('Pinned chats')).toBeNull();
  });

  it('once the lists are in, shows only what they hold', () => {
    remember('pinnedRows', 3);
    renderSections();

    act(() => useChatStore.setState({ conversations: [chat('a')], sharedConversations: [], conversationsLoadedAt: Date.now() }));

    expect(screen.queryByText('Pinned chats')).toBeNull();
  });

  it('a first load that failed keeps no room: the error shows instead', () => {
    remember('pinnedRows', 3);
    renderSections();

    act(() => useChatStore.setState({ conversationsError: 'Failed' }));

    expect(screen.queryByText('Pinned chats')).toBeNull();
  });
});

describe('the skeleton that stays a moment after the lists arrive', () => {
  const shown = (n: number, prefix: string, extra: Partial<Conversation> = {}) =>
    Array.from({ length: n }, (_, i) => ({ ...chat(`${prefix}${i}`), ...extra }) as Conversation);

  it('has the rows each section is about to show, so nothing under it moves twice', async () => {
    remember('pinnedRows', 2);
    remember('sharedRows', 6);
    renderSections();
    act(() => useChatStore.setState({ isConversationsLoading: true }));
    // Past the show delay, the skeletons are on screen.
    await act(async () => new Promise((resolve) => setTimeout(resolve, 200)));
    expect(skeletonRows()).toEqual([2, 6, 3]);

    act(() =>
      useChatStore.setState({
        conversations: [...shown(2, 'p', { isPinned: true }), ...shown(3, 'o')],
        sharedConversations: shown(8, 's'),
        isConversationsLoading: false,
        conversationsLoadedAt: Date.now(),
      }),
    );

    // Still fading out: Pinned's two, Shared chats' five and its More.
    expect(skeletonRows()).toEqual([2, 6, 3]);
  });
});

describe('a memory older than a week', () => {
  it('counts for nothing: no room kept, and the Projects row waits for the flags', () => {
    const old = 8 * DAY_MS;
    remember('pinnedRows', 3, old);
    remember('sharedRows', 6, old);
    remember('projects', 1, old);
    useFeatureFlagsStore.setState({ flags: null } as never);

    render(
      <Theme>
        <StaticNavSection />
        <ChatSections onOpenMoreChats={() => {}} />
      </Theme>,
    );

    expect(screen.queryByText('Pinned chats')).toBeNull();
    expect(screen.queryByText(SHARED())).toBeNull();
    expect(screen.queryByText('Projects')).toBeNull();
  });

  it('a memory from six days ago still counts', () => {
    remember('sharedRows', 2, 6 * DAY_MS);
    renderSections();
    expect(skeletonRows()).toEqual([2]);
  });
});

describe('what the next first load goes by', () => {
  const railKeys = () =>
    Object.keys(window.localStorage)
      .filter((k) => k.startsWith('pipeshub.rail.'))
      .sort()
      .map((k) => [k, stored(k.split('.').pop()!)?.v]);

  it('once the lists are in: the rows Pinned and Shared chats show, their More included; nothing else', () => {
    renderSections();
    expect(railKeys()).toEqual([]);

    act(() =>
      useChatStore.setState({
        conversations: [...['p1', 'p2', 'p3', 'p4', 'p5', 'p6'].map(pin), chat('a')],
        sharedConversations: [chat('s')],
        conversationsLoadedAt: Date.now(),
      }),
    );

    expect(railKeys()).toEqual([
      ['pipeshub.rail.user-1.pinnedRows', 6],
      ['pipeshub.rail.user-1.sharedRows', 1],
    ]);
    expect(Object.values(window.localStorage).join(' ')).not.toContain('Chat ');
  });

  it('follows a pin made later, with no load in between', () => {
    renderSections();
    act(() => useChatStore.setState({ conversations: [chat('a'), chat('b')], sharedConversations: [], conversationsLoadedAt: Date.now() }));
    expect(stored('pinnedRows')?.v).toBe(0);

    act(() => {
      useChatStore.getState().setConversationPinned('a', true);
    });

    expect(stored('pinnedRows')?.v).toBe(1);
  });

  it('a Shared chats section left with only pinned chats keeps no room next time', () => {
    renderSections();

    act(() => useChatStore.setState({ conversations: [], sharedConversations: [pin('s')], conversationsLoadedAt: Date.now() }));

    expect(stored('sharedRows')?.v).toBe(0);
    expect(stored('pinnedRows')?.v).toBe(1);
  });

  it('is written again after each load, the same rows too, so a tab left open keeps it from going stale', () => {
    const start = Date.now();
    const clock = vi.spyOn(Date, 'now').mockReturnValue(start);
    try {
      renderSections();
      act(() => useChatStore.setState({ sharedConversations: [chat('s')], conversationsLoadedAt: Date.now() }));
      expect(stored('sharedRows')).toEqual({ v: 1, at: start });

      // Three days on, a quiet refresh brings the same rows.
      clock.mockReturnValue(start + 3 * DAY_MS);
      act(() => useChatStore.setState({ sharedConversations: [chat('s')], conversationsLoadedAt: Date.now() }));

      expect(stored('sharedRows')).toEqual({ v: 1, at: start + 3 * DAY_MS });
    } finally {
      clock.mockRestore();
    }
  });
});

describe('a section that is loading', () => {
  const section = (isLoading: boolean) => (
    <ChatSection
      variant="rail"
      title="Shared chats"
      conversations={[chat('a')]}
      isLoading={isLoading}
      hasError={false}
      currentConversationId={null}
      onSelectConversation={() => {}}
      onNewChat={() => {}}
      skeletonCount={6}
      hasMore
      onMore={() => {}}
    />
  );

  it('shows its More with its rows, not under the skeleton, where it would move down when they come', () => {
    const view = render(section(true));
    expect(screen.queryByText('More')).toBeNull();

    view.rerender(section(false));

    // Once the skeleton has gone (it stays a moment, so it never blinks).
    return vi.waitFor(() => expect(screen.getByText('More')).toBeTruthy());
  });
});
