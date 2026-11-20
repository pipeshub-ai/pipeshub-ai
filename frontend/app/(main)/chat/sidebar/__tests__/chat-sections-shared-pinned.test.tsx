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

describe('shared chats you pinned join your own pins in the one Pinned section', () => {
  it('with nothing pinned: Shared chats and Your chats as they are, no Pinned section', () => {
    renderSections([shared('s1', HOUR), shared('s2', 2 * HOUR)], [own('o1', HOUR)]);

    expect(railOrder()).toEqual(['#Shared chats', 's1', 's2', '#Your chats', 'o1']);
  });

  it('lists your pins of both lists together, newest activity first, above Shared chats and Your chats', () => {
    renderSections(
      [shared('s1', HOUR), shared('s2', 2 * HOUR, { isPinned: true }), shared('s3', 4 * HOUR)],
      [own('o1', 3 * HOUR, { isPinned: true }), own('o2', HOUR / 2, { isPinned: true }), own('o3', 5 * HOUR)],
    );

    expect(railOrder()).toEqual(['#Pinned chats', 'o2', 's2', 'o1', '#Shared chats', 's1', 's3', '#Your chats', 'o3']);
    // Pinned and Shared chats have no labels inside; Your chats files o3 under its day.
    const labels = Array.from(document.querySelectorAll('[data-sidebar-sub-label]'));
    expect(labels.map((el) => el.textContent)).toEqual(['Today']);
    expect(labels[0].compareDocumentPosition(screen.getByText('Your chats')) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it('keeps a shared chat’s “Shared by” line in Pinned, so it still reads as shared', () => {
    renderSections([shared('s1', HOUR, { isPinned: true, sharedBy: { userId: 'u9', name: 'Maya Chen' } })]);

    expect(railOrder()).toEqual(['#Pinned chats', 's1Shared by Maya Chen', '#Your chats']);
    expect(screen.getByText('Shared by Maya Chen')).toBeTruthy();
  });

  it('hides Shared chats when every shared chat is pinned, and shows it with only More when the server has others', () => {
    renderSections([shared('s1', HOUR, { isPinned: true })], [own('o1', HOUR)]);
    expect(railOrder()).toEqual(['#Pinned chats', 's1', '#Your chats', 'o1']);
    expect(screen.queryByText('No shared chats')).toBeNull();
    cleanup();

    renderSections([shared('s1', HOUR, { isPinned: true })], [own('o1', HOUR)], true);
    expect(railOrder()).toEqual(['#Pinned chats', 's1', '#Shared chats', 'More', '#Your chats', 'o1']);
    expect(screen.queryByText('No shared chats')).toBeNull();
  });

  it('6 shared pins and 6 others: Pinned shows 5 and More, Shared chats its own 5 and More', () => {
    const pins = Array.from({ length: 6 }, (_, i) => shared(`p${String(i + 1)}`, (i + 1) * HOUR, { isPinned: true }));
    const rest = Array.from({ length: 6 }, (_, i) => shared(`s${String(i + 11)}`, (i + 11) * HOUR));
    renderSections([...pins, ...rest]);

    expect(railOrder()).toEqual([
      '#Pinned chats', 'p1', 'p2', 'p3', 'p4', 'p5', 'More',
      '#Shared chats', 's11', 's12', 's13', 's14', 's15', 'More',
      '#Your chats',
    ]);
  });

  it('a pin moves a shared chat into Pinned and an unpin back to Shared chats in its place, with no reload', () => {
    renderSections([shared('s1', HOUR), shared('s2', 2 * HOUR), shared('s3', 3 * HOUR)]);

    act(() => {
      useChatStore.getState().setConversationPinned('s2', true);
    });
    expect(railOrder()).toEqual(['#Pinned chats', 's2', '#Shared chats', 's1', 's3', '#Your chats']);

    act(() => {
      useChatStore.getState().setConversationPinned('s2', false);
    });
    expect(railOrder()).toEqual(['#Shared chats', 's1', 's2', 's3', '#Your chats']);
    expect(useChatStore.getState().conversationsVersion).toBe(0);
  });

  it('a new message in a pinned chat puts it first in Pinned at once, as Shared chats and Your chats do with theirs', () => {
    renderSections(
      [shared('s1', HOUR / 2, { isPinned: true }), shared('s2', 3 * HOUR, { isPinned: true }), shared('s3', 4 * HOUR), shared('s4', 5 * HOUR)],
      [own('o1', HOUR, { isPinned: true }), own('o2', 2 * HOUR, { isPinned: true }), own('o3', 6 * HOUR), own('o4', 7 * HOUR)],
    );
    expect(railOrder()).toEqual(['#Pinned chats', 's1', 'o1', 'o2', 's2', '#Shared chats', 's3', 's4', '#Your chats', 'o3', 'o4']);
    // What the end of an answer does: the chat moves up in the list it is in, with the time the server gave it.
    let serverTime = NOW;
    const newMessageIn = (id: string) =>
      act(() => useChatStore.getState().moveConversationToTop(id, { lastActivityAt: (serverTime += 1000) }));

    newMessageIn('o2');
    expect(railOrder()).toEqual(['#Pinned chats', 'o2', 's1', 'o1', 's2', '#Shared chats', 's3', 's4', '#Your chats', 'o3', 'o4']);
    // s1 is still first in its own list, but no longer in Pinned.
    newMessageIn('s1');
    expect(railOrder()).toEqual(['#Pinned chats', 's1', 'o2', 'o1', 's2', '#Shared chats', 's3', 's4', '#Your chats', 'o3', 'o4']);
    newMessageIn('s2');
    newMessageIn('s4');
    newMessageIn('o4');
    expect(railOrder()).toEqual(['#Pinned chats', 's2', 's1', 'o2', 'o1', '#Shared chats', 's4', 's3', '#Your chats', 'o4', 'o3']);
  });

  it('a shared pin the server refuses goes back to Shared chats, in its place', () => {
    renderSections([shared('s1', HOUR), shared('s2', 2 * HOUR), shared('s3', 3 * HOUR)]);

    let undo: () => void = () => {};
    act(() => {
      undo = useChatStore.getState().setConversationPinned('s3', true);
    });
    expect(railOrder()).toEqual(['#Pinned chats', 's3', '#Shared chats', 's1', 's2', '#Your chats']);

    act(() => undo());
    expect(railOrder()).toEqual(['#Shared chats', 's1', 's2', 's3', '#Your chats']);
  });
});
