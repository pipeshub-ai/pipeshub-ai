import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, screen } from '@testing-library/react';
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
const NOW = new Date(2026, 0, 15, 12, 0, 0).getTime();
const HOUR = 60 * 60 * 1000;

/** A chat shared with the viewer: `isPinned` is the viewer's own pin, as the shared list sends it. */
function shared(id: string, ageMs: number, extra: Partial<Conversation> = {}): Conversation {
  return {
    id,
    title: id,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    isShared: true,
    sharedWith: [],
    lastActivityAt: NOW - ageMs,
    isOwner: false,
    ...extra,
  };
}

const own = (id: string, ageMs: number, extra: Partial<Conversation> = {}) => shared(id, ageMs, { isShared: false, isOwner: true, ...extra });
const page = (hasNextPage: boolean) => ({ page: 1, limit: 10, totalCount: 0, totalPages: 1, hasNextPage, hasPrevPage: false });

function renderSections(sharedConversations: Conversation[], conversations: Conversation[] = [], sharedHasNextPage = false) {
  useChatStore.setState({
    conversations,
    sharedConversations,
    isConversationsLoading: false,
    conversationsError: null,
    sharedPagination: page(sharedHasNextPage),
    pagination: page(false),
  });
  renderInTheme(<ChatSections onOpenMoreChats={vi.fn()} />);
}

const HEADINGS = new Set(['Pinned chats', 'Shared chats', 'Your chats']);

const sharedPins = (n: number) => Array.from({ length: n }, (_, i) => shared(`p${String(i + 1)}`, (i + 1) * HOUR, { isPinned: true }));
const sharedOthers = (n: number) => Array.from({ length: n }, (_, i) => shared(`s${String(i + 21)}`, (i + 21) * HOUR));
const titles = (prefix: string, from: number, n: number) => Array.from({ length: n }, (_, i) => `${prefix}${String(from + i)}`);

/** Section headings (`#…`), row titles and More, top to bottom. */
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

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  installBrowserShims();
  useChatStore.setState({ ...initialState });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('a shared chat you pinned leads Shared chats, as yours lead Your chats: no section of their own', () => {
  it('with nothing pinned: Shared chats and Your chats as they are', () => {
    renderSections([shared('s1', HOUR), shared('s2', 2 * HOUR)], [own('o1', HOUR)]);

    expect(railOrder()).toEqual(['#Shared chats', 's1', 's2', '#Your chats', 'o1']);
  });

  it('each pin stays in its own list, at the top, newest activity first; there is no Pinned chats heading', () => {
    renderSections(
      [shared('s1', HOUR), shared('s2', 2 * HOUR, { isPinned: true }), shared('s3', 4 * HOUR)],
      [own('o1', 3 * HOUR, { isPinned: true }), own('o2', HOUR / 2, { isPinned: true }), own('o3', 5 * HOUR)],
    );

    expect(railOrder()).toEqual(['#Shared chats', 's2', 's1', 's3', '#Your chats', 'o2', 'o1', 'o3']);
    expect(screen.queryByText('Pinned chats')).toBeNull();
    // Shared chats has no labels inside; Your chats files only o3 under its day, under the pins.
    const labels = Array.from(document.querySelectorAll('[data-sidebar-sub-label]'));
    expect(labels.map((el) => el.textContent)).toEqual(['Today']);
    expect(screen.getByText('o1').compareDocumentPosition(labels[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(labels[0].compareDocumentPosition(screen.getByText('o3')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('lists several shared pins newest activity first, however long ago they were active', () => {
    renderSections([shared('s1', HOUR), shared('p1', 40 * HOUR, { isPinned: true }), shared('p2', 2 * HOUR, { isPinned: true })]);

    expect(railOrder()).toEqual(['#Shared chats', 'p2', 'p1', 's1', '#Your chats']);
  });

  it('every shared chat pinned: Shared chats lists them, with More after them when the server has others', () => {
    renderSections([shared('s1', HOUR, { isPinned: true })], [own('o1', HOUR)]);
    expect(railOrder()).toEqual(['#Shared chats', 's1', '#Your chats', 'o1']);
    expect(screen.queryByText('No shared chats')).toBeNull();
    cleanup();

    renderSections([shared('s1', HOUR, { isPinned: true })], [own('o1', HOUR)], true);
    expect(railOrder()).toEqual(['#Shared chats', 's1', 'More', '#Your chats', 'o1']);
    expect(screen.queryByText('No shared chats')).toBeNull();
  });

  it('pins do not use up the three: 8 shared pins and 6 others show all 8, the 3 newest others, then More', () => {
    renderSections([...sharedOthers(6), ...sharedPins(8)]);

    expect(railOrder()).toEqual(['#Shared chats', ...titles('p', 1, 8), ...titles('s', 21, 3), 'More', '#Your chats']);
  });

  it('many pins: 12 shared pins and 8 others show all 12, the 3 newest others, then More', () => {
    renderSections([...sharedOthers(8), ...sharedPins(12)]);

    expect(railOrder()).toEqual(['#Shared chats', ...titles('p', 1, 12), ...titles('s', 21, 3), 'More', '#Your chats']);
    expect(document.querySelectorAll('[data-pinned-mark]')).toHaveLength(12);
  });

  it('a pin moves a shared chat to the top of Shared chats and an unpin puts it back in its place, with no reload', () => {
    renderSections([shared('s1', HOUR), shared('s2', 2 * HOUR), shared('s3', 3 * HOUR)]);

    act(() => {
      useChatStore.getState().setConversationPinned('s2', true);
    });
    expect(railOrder()).toEqual(['#Shared chats', 's2', 's1', 's3', '#Your chats']);

    act(() => {
      useChatStore.getState().setConversationPinned('s2', false);
    });
    expect(railOrder()).toEqual(['#Shared chats', 's1', 's2', 's3', '#Your chats']);
    expect(useChatStore.getState().conversationsVersion).toBe(0);
  });

  it('a new message in a pinned chat puts it first among its own list’s pins at once', () => {
    renderSections(
      [shared('s1', HOUR / 2, { isPinned: true }), shared('s2', 3 * HOUR, { isPinned: true }), shared('s3', 4 * HOUR), shared('s4', 5 * HOUR)],
      [own('o1', HOUR, { isPinned: true }), own('o2', 2 * HOUR, { isPinned: true }), own('o3', 6 * HOUR), own('o4', 7 * HOUR)],
    );
    expect(railOrder()).toEqual(['#Shared chats', 's1', 's2', 's3', 's4', '#Your chats', 'o1', 'o2', 'o3', 'o4']);
    // What the end of an answer does: the chat moves up in the list it is in, with the time the server gave it.
    let serverTime = NOW;
    const newMessageIn = (id: string) =>
      act(() => useChatStore.getState().moveConversationToTop(id, { lastActivityAt: (serverTime += 1000) }));

    newMessageIn('o2');
    expect(railOrder()).toEqual(['#Shared chats', 's1', 's2', 's3', 's4', '#Your chats', 'o2', 'o1', 'o3', 'o4']);
    newMessageIn('s2');
    newMessageIn('s4');
    newMessageIn('o4');
    expect(railOrder()).toEqual(['#Shared chats', 's2', 's1', 's4', 's3', '#Your chats', 'o2', 'o1', 'o4', 'o3']);
  });

  it('a shared pin the server refuses goes back to its place in Shared chats', () => {
    renderSections([shared('s1', HOUR), shared('s2', 2 * HOUR), shared('s3', 3 * HOUR)]);

    let undo: () => void = () => {};
    act(() => {
      undo = useChatStore.getState().setConversationPinned('s3', true);
    });
    expect(railOrder()).toEqual(['#Shared chats', 's3', 's1', 's2', '#Your chats']);

    act(() => undo());
    expect(railOrder()).toEqual(['#Shared chats', 's1', 's2', 's3', '#Your chats']);
  });
});
