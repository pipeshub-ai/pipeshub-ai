import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { getI18n } from 'react-i18next';
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

const { useChatStore } = await import('@/chat/store');
const { ChatSections } = await import('../chat-sections');

const initialState = useChatStore.getState();
// Midday, so no fixture crosses local midnight.
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

const page = (hasNextPage: boolean) => ({ page: 1, limit: 10, totalCount: 0, totalPages: 1, hasNextPage, hasPrevPage: false });

function renderSections(conversations: Conversation[], hasNextPage = false, onOpenMoreChats = vi.fn()) {
  useChatStore.setState({
    conversations,
    sharedConversations: [],
    isConversationsLoading: false,
    conversationsError: null,
    pagination: page(hasNextPage),
  });
  renderInTheme(<ChatSections onOpenMoreChats={onOpenMoreChats} />);
  return onOpenMoreChats;
}

const HEADINGS = new Set(['Pinned chats', 'Your chats']);

/** Every section heading, row title and More, top to bottom: `#Your chats`, `p1`, `More`. */
function railOrder(): string[] {
  const out: string[] = [];
  document.querySelectorAll('a, span, [role=button]').forEach((el) => {
    if (el.tagName === 'A') out.push((el.textContent ?? '').replace(', pinned', '').trim());
    else if (el.getAttribute('role') === 'button') out.push((el.textContent ?? '').trim());
    else if (el.children.length === 0) {
      const text = (el.textContent ?? '').trim();
      // The shared section's name comes from the translations, which an edition may word differently.
      if (text === getI18n().t('chat.sharedChats')) out.push('#Shared chats');
      else if (HEADINGS.has(text)) out.push(`#${text}`);
    }
  });
  return out;
}

/** Your chats' day labels (`~Today`) and every row title, top to bottom: a pinned row shows before the first label. */
function daysAndRows(): string[] {
  return Array.from(document.querySelectorAll('[data-sidebar-sub-label], a')).map((el) =>
    el.tagName === 'A' ? (el.textContent ?? '').replace(', pinned', '').trim() : `~${(el.textContent ?? '').trim()}`,
  );
}

const pins = (n: number) => Array.from({ length: n }, (_, i) => chat(`p${String(i + 1)}`, (i + 1) * HOUR, { isPinned: true }));
const others = (n: number) => Array.from({ length: n }, (_, i) => chat(`r${String(i + 1)}`, (i + 1) * DAY));
const titles = (prefix: string, n: number) => Array.from({ length: n }, (_, i) => `${prefix}${String(i + 1)}`);

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

describe('your pinned chats lead Your chats, with no section of their own', () => {
  it('with nothing pinned, Your chats lists every chat, by day', () => {
    renderSections([chat('r1', HOUR), chat('r2', 2 * HOUR), chat('r3', DAY)]);

    expect(railOrder()).toEqual(['#Your chats', 'r1', 'r2', 'r3']);
    expect(daysAndRows()).toEqual(['~Today', 'r1', 'r2', '~Yesterday', 'r3']);
  });

  it('a pinned chat is first in Your chats, above Today with no label of its own; there is no Pinned chats heading', () => {
    renderSections([chat('r1', HOUR), chat('p1', 2 * HOUR, { isPinned: true }), chat('r2', DAY)]);

    expect(railOrder()).toEqual(['#Your chats', 'p1', 'r1', 'r2']);
    // p1 is from today too, but it is listed once, above the days.
    expect(daysAndRows()).toEqual(['p1', '~Today', 'r1', '~Yesterday', 'r2']);
    for (const label of ['Pinned chats', 'Pinned', 'Recent', 'Last 7 days', 'Older']) {
      expect(screen.queryByText(label)).toBeNull();
    }
    expect(screen.getAllByRole('button', { name: 'New chat' })).toHaveLength(1);
  });

  it('lists several pinned chats newest activity first, however long ago they were active', () => {
    renderSections([
      chat('r1', HOUR),
      chat('p1', 40 * DAY, { isPinned: true }),
      chat('p2', 2 * HOUR, { isPinned: true }),
    ]);

    expect(railOrder()).toEqual(['#Your chats', 'p2', 'p1', 'r1']);
  });

  it('pins do not use up the five: 7 pinned and 6 others show all 7, the 5 newest others and a More for Your chats', () => {
    const onOpen = renderSections([...others(6), ...pins(7)]);

    expect(railOrder()).toEqual(['#Your chats', ...titles('p', 7), ...titles('r', 5), 'More']);
    expect(screen.getAllByText('More')).toHaveLength(1);
    fireEvent.click(screen.getByText('More'));
    expect(onOpen).toHaveBeenLastCalledWith('your');
  });

  it('many pins: 12 pinned and 8 others show all 12, then the 5 newest others, then More', () => {
    renderSections([...others(8), ...pins(12)]);

    expect(railOrder()).toEqual(['#Your chats', ...titles('p', 12), ...titles('r', 5), 'More']);
    expect(daysAndRows()).toEqual([...titles('p', 12), '~Yesterday', 'r1', '~Last 7 days', 'r2', 'r3', 'r4', 'r5']);
    expect(document.querySelectorAll('[data-pinned-mark]')).toHaveLength(12);
  });

  it('with five pinned and no next page, shows them all and no More', () => {
    renderSections(pins(5));

    expect(railOrder()).toEqual(['#Your chats', ...titles('p', 5)]);
    expect(screen.queryByText('More')).toBeNull();
  });

  it('every loaded chat pinned with another page on the server: the pins, then More to reach the rest', () => {
    const onOpen = renderSections(pins(2), true);

    expect(railOrder()).toEqual(['#Your chats', 'p1', 'p2', 'More']);
    expect(screen.queryByText('Your chats will appear here')).toBeNull();
    fireEvent.click(screen.getByText('More'));
    expect(onOpen).toHaveBeenLastCalledWith('your');
  });

  it('every chat of yours pinned and nothing more: the pins are the list, with no line under them', () => {
    renderSections(pins(2));

    expect(railOrder()).toEqual(['#Your chats', 'p1', 'p2']);
    expect(screen.queryByText('Your chats will appear here')).toBeNull();
    expect(screen.getByRole('button', { name: 'New chat' })).toBeTruthy();
  });

  it('with no chats of yours at all, Your chats keeps its line; a pinned shared chat leads Shared chats instead', () => {
    useChatStore.setState({ sharedConversations: [chat('s1', HOUR, { isOwner: false, isShared: true, isPinned: true })] });
    renderInTheme(<ChatSections onOpenMoreChats={vi.fn()} />);

    expect(railOrder()).toEqual(['#Shared chats', 's1', '#Your chats']);
    expect(screen.getByText('Your chats will appear here')).toBeTruthy();
  });

  it('pinned rows wear the pin right after their title, hidden from screen readers; the rest wear none', () => {
    renderSections([chat('r1', HOUR), chat('p1', 2 * HOUR, { isPinned: true }), chat('p2', 3 * HOUR, { isPinned: true })]);

    const marks = Array.from(document.querySelectorAll('[data-pinned-mark]'));
    expect(marks).toHaveLength(2);
    for (const title of ['p1', 'p2']) {
      expect(screen.getByText(title).nextElementSibling?.hasAttribute('data-pinned-mark')).toBe(true);
      expect(screen.getByRole('link', { name: new RegExp(`^${title} ?, pinned$`) })).toBeTruthy();
    }
    marks.forEach((mark) => expect(mark.getAttribute('aria-hidden')).toBe('true'));
    expect(screen.getByText('r1').nextElementSibling).toBeNull();
  });

  it('a chat still being written shows under Today, below the pins', () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().addPendingConversation(slotId);
    renderSections([chat('p1', HOUR, { isPinned: true })]);

    const order = Array.from(document.querySelectorAll('a, .generating-shimmer-base, [data-sidebar-sub-label], span')).flatMap((el) => {
      const text = (el.textContent ?? '').replace(', pinned', '').trim();
      if (el.matches('.generating-shimmer-base') || el.tagName === 'A') return [text];
      if (el.hasAttribute('data-sidebar-sub-label')) return [`~${text}`];
      return el.children.length === 0 && HEADINGS.has(text) ? [`#${text}`] : [];
    });
    expect(order).toEqual(['#Your chats', 'p1', '~Today', 'Generating chat…']);
  });

  it('a pin moves the row to the top of Your chats and an unpin moves it back, at once, with no Pinned heading', () => {
    renderSections([chat('r1', HOUR), chat('r2', 2 * HOUR)]);

    act(() => {
      useChatStore.getState().setConversationPinned('r2', true);
    });
    expect(railOrder()).toEqual(['#Your chats', 'r2', 'r1']);
    expect(screen.queryByText('Pinned chats')).toBeNull();

    act(() => {
      useChatStore.getState().setConversationPinned('r2', false);
    });
    expect(railOrder()).toEqual(['#Your chats', 'r1', 'r2']);
  });

  it('a pin takes the row out of its day, the day going when it empties; an unpin puts it back under its day, at once', () => {
    renderSections([chat('r1', HOUR), chat('r2', DAY)]);
    expect(daysAndRows()).toEqual(['~Today', 'r1', '~Yesterday', 'r2']);

    act(() => {
      useChatStore.getState().setConversationPinned('r2', true);
    });
    expect(daysAndRows()).toEqual(['r2', '~Today', 'r1']);

    act(() => {
      useChatStore.getState().setConversationPinned('r2', false);
    });
    expect(daysAndRows()).toEqual(['~Today', 'r1', '~Yesterday', 'r2']);
  });

  it('a pin the server refuses puts the row back in Your chats, in its place', () => {
    renderSections([chat('r1', HOUR), chat('r2', 2 * HOUR), chat('r3', 3 * HOUR)]);

    let undo: () => void = () => {};
    act(() => {
      undo = useChatStore.getState().setConversationPinned('r3', true);
    });
    expect(railOrder()).toEqual(['#Your chats', 'r3', 'r1', 'r2']);

    act(() => undo());
    expect(railOrder()).toEqual(['#Your chats', 'r1', 'r2', 'r3']);
  });
});
