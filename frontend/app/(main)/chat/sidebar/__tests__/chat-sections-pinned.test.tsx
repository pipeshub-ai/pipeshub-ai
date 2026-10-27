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
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const { useChatStore } = await import('@/chat/store');
const { ChatSections } = await import('../chat-sections');

const initialState = useChatStore.getState();
// Midday, so no fixture (up to 9 hours old) crosses local midnight into another day group.
const NOW = new Date(2026, 0, 15, 12, 0, 0).getTime();
const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

function chat(id: string, ageMs: number, extra: Partial<Conversation> = {}): Conversation {
  return {
    id,
    title: id,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    isShared: false,
    sharedWith: [],
    lastActivityAt: NOW - ageMs,
    isOwner: true,
    ...extra,
  };
}

function renderSections(conversations: Conversation[], hasNextPage = false) {
  useChatStore.setState({
    conversations,
    sharedConversations: [],
    isConversationsLoading: false,
    conversationsError: null,
    pagination: { page: 1, limit: 20, totalCount: conversations.length, totalPages: 1, hasNextPage, hasPrevPage: false },
  });
  renderInTheme(<ChatSections onOpenMoreChats={vi.fn()} />);
  fireEvent.click(screen.getByText('Recents'));
}

/** Text of every chat row and group heading, top to bottom. */
function sidebarOrder(): string[] {
  const wanted = new Set(['Pinned', 'Today', 'Yesterday', 'Last 7 days', 'Older']);
  const out: string[] = [];
  document.querySelectorAll('a, span').forEach((el) => {
    const text = (el.textContent ?? '').trim();
    if (el.tagName === 'A' && /^(p|r)\d+$/.test(text)) out.push(text);
    if (el.tagName === 'SPAN' && wanted.has(text) && el.children.length === 0) out.push(text);
  });
  return out;
}

beforeEach(() => {
  // Only Date is faked: Radix and React timers must keep running.
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  installBrowserShims();
  useChatStore.setState({ ...initialState });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('the Your Chats list in the sidebar', () => {
  it('shows a Pinned heading above Today, with the pinned chat under it and not again under its day', () => {
    renderSections([chat('r1', HOUR), chat('p1', 2 * HOUR, { isPinned: true })]);

    expect(sidebarOrder()).toEqual(['Pinned', 'p1', 'Today', 'r1']);
  });

  it('shows no Pinned heading when nothing is pinned', () => {
    renderSections([chat('r1', HOUR), chat('r2', DAY)]);

    expect(sidebarOrder()).toEqual(['Today', 'r1', 'Yesterday', 'r2']);
    expect(screen.queryByText('Pinned')).toBeNull();
  });

  it('keeps a chat pinned long ago in Pinned, not in Older', () => {
    renderSections([chat('r1', HOUR), chat('p1', 40 * DAY, { isPinned: true })]);

    expect(sidebarOrder()).toEqual(['Pinned', 'p1', 'Today', 'r1']);
  });

  it('lists several pinned chats newest activity first', () => {
    renderSections([
      chat('p1', 5 * DAY, { isPinned: true }),
      chat('p2', HOUR, { isPinned: true }),
      chat('r1', 2 * HOUR),
    ]);

    expect(sidebarOrder()).toEqual(['Pinned', 'p2', 'p1', 'Today', 'r1']);
  });

  it('still shows a pinned chat that the server list put after the 10 shown, ahead of unpinned ones', () => {
    // Twelve chats; the pinned one is the oldest and sits last in the array.
    const unpinned = Array.from({ length: 11 }, (_, i) => chat(`r${String(i + 1)}`, (i + 1) * HOUR));
    renderSections([...unpinned, chat('p1', 3 * DAY, { isPinned: true })]);

    const order = sidebarOrder();
    expect(order.slice(0, 3)).toEqual(['Pinned', 'p1', 'Today']);
    expect(order.filter((t) => t.startsWith('r'))).toHaveLength(9);
    expect(screen.getByText('More')).toBeTruthy();
  });
});
