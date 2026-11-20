import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
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

const HEADINGS = new Set(['Pinned chats', 'Shared chats', 'Your chats']);

/** Every section heading, row title and More, top to bottom: `#Pinned`, `p1`, `More`. */
function railOrder(): string[] {
  const out: string[] = [];
  document.querySelectorAll('a, span, [role=button]').forEach((el) => {
    if (el.tagName === 'A') out.push((el.textContent ?? '').replace(', pinned', '').trim());
    else if (el.getAttribute('role') === 'button') out.push((el.textContent ?? '').trim());
    else if (el.children.length === 0 && HEADINGS.has((el.textContent ?? '').trim())) out.push(`#${el.textContent!.trim()}`);
  });
  return out;
}

/** Your chats' day labels (`~Today`) and every row title, top to bottom: a pinned row shows before the first label. */
function daysAndRows(): string[] {
  return Array.from(document.querySelectorAll('[data-sidebar-sub-label], a')).map((el) =>
    el.tagName === 'A' ? (el.textContent ?? '').replace(', pinned', '').trim() : `~${(el.textContent ?? '').trim()}`,
  );
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

describe('the Pinned section of the rail, with your own pins', () => {
  it('with nothing pinned there is no Pinned section, and Your chats lists every chat, by day', () => {
    renderSections([chat('r1', HOUR), chat('r2', 2 * HOUR), chat('r3', DAY)]);

    expect(railOrder()).toEqual(['#Your chats', 'r1', 'r2', 'r3']);
    expect(daysAndRows()).toEqual(['~Today', 'r1', 'r2', '~Yesterday', 'r3']);
  });

  it('lists pinned chats in their own section above Your chats, which files only the rest under their days', () => {
    renderSections([chat('r1', HOUR), chat('p1', 2 * HOUR, { isPinned: true }), chat('r2', DAY)]);

    expect(railOrder()).toEqual(['#Pinned chats', 'p1', '#Your chats', 'r1', 'r2']);
    // p1 is from today too, but it is listed once, in Pinned, with no day of its own.
    expect(daysAndRows()).toEqual(['p1', '~Today', 'r1', '~Yesterday', 'r2']);
    for (const label of ['Recent', 'Last 7 days', 'Older']) {
      expect(screen.queryByText(label)).toBeNull();
    }
  });

  it('gives Pinned the section heading Your chats has, with no + of its own', () => {
    renderSections([chat('p1', HOUR, { isPinned: true }), chat('r1', 2 * HOUR)]);

    const pinned = screen.getByText('Pinned chats');
    const yours = screen.getByText('Your chats');
    expect(pinned.tagName).toBe(yours.tagName);
    expect(pinned.getAttribute('style')).toBe(yours.getAttribute('style'));
    expect(pinned.closest('button')).toBeNull();
    expect(screen.getAllByRole('button', { name: 'New chat' })).toHaveLength(1);
  });

  it('lists several pinned chats newest activity first, however long ago they were active', () => {
    renderSections([
      chat('r1', HOUR),
      chat('p1', 40 * DAY, { isPinned: true }),
      chat('p2', 2 * HOUR, { isPinned: true }),
    ]);

    expect(railOrder()).toEqual(['#Pinned chats', 'p2', 'p1', '#Your chats', 'r1']);
  });

  it('with 7 pinned: 5 rows and a More that opens the Pinned list; Your chats has its own More', () => {
    const pinned = Array.from({ length: 7 }, (_, i) => chat(`p${String(i + 1)}`, (i + 1) * HOUR, { isPinned: true }));
    const others = Array.from({ length: 6 }, (_, i) => chat(`r${String(i + 1)}`, (i + 1) * DAY));
    const onOpen = renderSections([...others, ...pinned]);

    expect(railOrder()).toEqual([
      '#Pinned chats', 'p1', 'p2', 'p3', 'p4', 'p5', 'More',
      '#Your chats', 'r1', 'r2', 'r3', 'r4', 'r5', 'More',
    ]);
    const [pinnedMore, yourMore] = screen.getAllByText('More');
    fireEvent.click(pinnedMore);
    expect(onOpen).toHaveBeenLastCalledWith('pinned');
    fireEvent.click(yourMore);
    expect(onOpen).toHaveBeenLastCalledWith('your');
  });

  it('with five pinned and no next page, shows them all and no More', () => {
    renderSections(Array.from({ length: 5 }, (_, i) => chat(`p${String(i + 1)}`, (i + 1) * HOUR, { isPinned: true })));

    expect(railOrder()).toEqual(['#Pinned chats', 'p1', 'p2', 'p3', 'p4', 'p5', '#Your chats']);
    expect(screen.queryByText('More')).toBeNull();
  });

  it('every loaded chat pinned with another page on the server: Your chats offers More to reach the rest', () => {
    const onOpen = renderSections([chat('p1', HOUR, { isPinned: true }), chat('p2', 2 * HOUR, { isPinned: true })], true);

    expect(railOrder()).toEqual(['#Pinned chats', 'p1', 'p2', '#Your chats', 'More']);
    expect(screen.queryByText('Your chats will appear here')).toBeNull();
    fireEvent.click(screen.getByText('More'));
    expect(onOpen).toHaveBeenLastCalledWith('your');
  });

  it('every chat of yours pinned and nothing more: Your chats keeps its heading and + but no line, they are in Pinned', () => {
    renderSections([chat('p1', HOUR, { isPinned: true }), chat('p2', 2 * HOUR, { isPinned: true })]);

    expect(railOrder()).toEqual(['#Pinned chats', 'p1', 'p2', '#Your chats']);
    expect(screen.queryByText('Your chats will appear here')).toBeNull();
    expect(screen.getByRole('button', { name: 'New chat' })).toBeTruthy();
  });

  it('with no chats of yours at all, Your chats keeps its line, even beside pinned shared chats', () => {
    useChatStore.setState({ sharedConversations: [chat('s1', HOUR, { isOwner: false, isShared: true, isPinned: true })] });
    renderInTheme(<ChatSections onOpenMoreChats={vi.fn()} />);

    expect(railOrder()).toEqual(['#Pinned chats', 's1', '#Your chats']);
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

  it('a chat still being written shows in Your chats, not in Pinned', () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().addPendingConversation(slotId);
    renderSections([chat('p1', HOUR, { isPinned: true })]);

    const order = Array.from(document.querySelectorAll('a, .generating-shimmer-base, span')).flatMap((el) => {
      const text = (el.textContent ?? '').replace(', pinned', '').trim();
      if (el.matches('.generating-shimmer-base')) return [text];
      if (el.tagName === 'A') return [text];
      return el.children.length === 0 && HEADINGS.has(text) ? [`#${text}`] : [];
    });
    expect(order).toEqual(['#Pinned chats', 'p1', '#Your chats', 'Generating chat…']);
  });

  it('a pin moves the row into Pinned and an unpin moves it back, at once; the last unpin removes the section', () => {
    renderSections([chat('r1', HOUR), chat('r2', 2 * HOUR)]);
    expect(screen.queryByText('Pinned chats')).toBeNull();

    act(() => {
      useChatStore.getState().setConversationPinned('r2', true);
    });
    expect(railOrder()).toEqual(['#Pinned chats', 'r2', '#Your chats', 'r1']);

    act(() => {
      useChatStore.getState().setConversationPinned('r2', false);
    });
    expect(screen.queryByText('Pinned chats')).toBeNull();
    expect(railOrder()).toEqual(['#Your chats', 'r1', 'r2']);
  });

  it('a pin takes the row out of its day, the day going when it empties; an unpin puts it back under its day, at once', () => {
    renderSections([chat('r1', HOUR), chat('r2', DAY)]);
    expect(daysAndRows()).toEqual(['~Today', 'r1', '~Yesterday', 'r2']);

    act(() => {
      useChatStore.getState().setConversationPinned('r2', true);
    });
    expect(railOrder()).toEqual(['#Pinned chats', 'r2', '#Your chats', 'r1']);
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
    expect(railOrder()).toEqual(['#Pinned chats', 'r3', '#Your chats', 'r1', 'r2']);

    act(() => undo());
    expect(railOrder()).toEqual(['#Your chats', 'r1', 'r2', 'r3']);
  });
});
