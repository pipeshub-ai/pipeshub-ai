/**
 * The rail and the More chats panel show one list of rows: deleting, archiving, renaming or pinning a chat in
 * either place changes both at once, with no reload, and Undo after an archive puts the row back where it was.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { useToastStore } from '@/lib/store/toast-store';
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

const api = vi.hoisted(() => ({
  fetchConversations: vi.fn(),
  deleteConversation: vi.fn(),
  archiveConversation: vi.fn(),
  restoreConversation: vi.fn(),
  renameConversation: vi.fn(),
  setConversationPinned: vi.fn(),
}));
vi.mock('@/chat/api', () => ({ ChatApi: api }));

const { useChatStore } = await import('@/chat/store');
const { ChatSections } = await import('../chat-sections');
const { MoreChatsSidebar } = await import('../more-chats-sidebar');

const initialState = useChatStore.getState();

function chat(id: string, title = `Chat ${id}`): Conversation {
  return {
    id,
    title,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    isShared: false,
    sharedWith: [],
    lastActivityAt: 1000,
    isOwner: true,
  };
}

// Newest activity first, as the server lists them.
const ALL = ['a', 'b', 'c', 'd'].map((id, i) => ({ ...chat(id), lastActivityAt: 4000 - i * 1000 }));

function Both() {
  return (
    <>
      <div data-testid="rail">
        <ChatSections onOpenMoreChats={vi.fn()} />
      </div>
      <div data-testid="more">
        <MoreChatsSidebar sectionType="your" onBack={vi.fn()} />
      </div>
    </>
  );
}

/** A row's title: its link text without the hidden ", pinned" a pinned row's name carries. */
const rowText = (el: Element) => (el.textContent ?? '').replace(', pinned', '').trim();

const HEADINGS = ['Pinned chats', 'Shared chats', 'Your chats'];
/** Titles of the rail's rows under one section heading. */
const railSection = (heading: string) => {
  const out: string[] = [];
  let current = '';
  screen.getByTestId('rail').querySelectorAll('a, span').forEach((el) => {
    const text = (el.textContent ?? '').trim();
    if (el.tagName === 'SPAN' && el.children.length === 0 && HEADINGS.includes(text)) current = text;
    else if (el.tagName === 'A' && current === heading) out.push(rowText(el));
  });
  return out;
};

const titlesIn = (testId: string) =>
  within(screen.getByTestId(testId))
    .queryAllByRole('link')
    .map(rowText);

async function choose(testId: string, title: string, item: string) {
  const link = within(screen.getByTestId(testId)).getByText(title).closest('a') as HTMLElement;
  const row = link.parentElement as HTMLElement;
  fireEvent.mouseEnter(row);
  const trigger = within(row).getByRole('button', { name: `Actions for ${title}` });
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('menuitem', { name: item }));
  });
  await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
}

async function deleteFrom(testId: string, title: string) {
  await choose(testId, title, 'Delete');
  const dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'DELETE' } });
  await act(async () => {
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
  });
}

async function renameFrom(testId: string, title: string, to: string) {
  await choose(testId, title, 'Rename');
  const field = await screen.findByRole('textbox', { name: 'New name' });
  fireEvent.change(field, { target: { value: to } });
  await act(async () => {
    fireEvent.keyDown(field, { key: 'Enter' });
    field.blur();
  });
}

const toasts = () => useToastStore.getState().toasts.filter((t) => !t.isExiting);

async function renderBoth() {
  renderInTheme(<Both />);
  await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat c', 'Chat d']));
  expect(titlesIn('rail')).toEqual(['Chat a', 'Chat b', 'Chat c', 'Chat d']);
}

beforeEach(() => {
  installBrowserShims();
  router.push.mockReset();
  router.replace.mockReset();
  useToastStore.setState({ toasts: [] });
  Object.values(api).forEach((fn) => {
    fn.mockReset();
    fn.mockResolvedValue(undefined);
  });
  api.fetchConversations.mockResolvedValue({
    conversations: ALL,
    pagination: { page: 1, limit: 20, totalCount: ALL.length, totalPages: 1, hasNextPage: false, hasPrevPage: false },
  });
  useChatStore.setState({
    ...initialState,
    conversations: ALL,
    sharedConversations: [],
    isConversationsLoading: false,
    conversationsVersion: 0,
    isMoreChatsPanelOpen: true,
    moreChatsSectionType: 'your',
  });
});
afterEach(() => cleanup());

describe('the rail and More chats stay in step', () => {
  it('delete in More: the chat leaves More and the rail', async () => {
    await renderBoth();
    await deleteFrom('more', 'Chat b');
    await waitFor(() => expect(api.deleteConversation).toHaveBeenCalledWith('b'));
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat c', 'Chat d']));
    expect(titlesIn('rail')).toEqual(['Chat a', 'Chat c', 'Chat d']);
    expect(api.fetchConversations).toHaveBeenCalledTimes(1);
  });

  it('delete in the rail: the chat leaves More', async () => {
    await renderBoth();
    await deleteFrom('rail', 'Chat c');
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat d']));
    expect(titlesIn('rail')).toEqual(['Chat a', 'Chat b', 'Chat d']);
  });

  it('archive in More: the chat leaves More and the rail', async () => {
    await renderBoth();
    await choose('more', 'Chat a', 'Archive');
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat b', 'Chat c', 'Chat d']));
    expect(titlesIn('rail')).toEqual(['Chat b', 'Chat c', 'Chat d']);
  });

  it('archive in the rail: the chat leaves More', async () => {
    await renderBoth();
    await choose('rail', 'Chat d', 'Archive');
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat c']));
    expect(titlesIn('rail')).toEqual(['Chat a', 'Chat b', 'Chat c']);
  });

  it('Undo after an archive puts the row back in More at its old place, once', async () => {
    await renderBoth();
    await choose('more', 'Chat b', 'Archive');
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat c', 'Chat d']));
    await waitFor(() => expect(toasts()).toHaveLength(1));

    act(() => toasts()[0].action!.onClick!());

    await waitFor(() => expect(api.restoreConversation).toHaveBeenCalledWith('b'));
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat c', 'Chat d']));
    // Once: a second restore for the same row adds nothing.
    const place = { key: useChatStore.getState().moreChats.key, row: chat('b'), index: 1 };
    act(() => useChatStore.getState().restoreMoreChatsRow(place));
    expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat c', 'Chat d']);
  });

  it('rename in More: the new title shows in More and the rail', async () => {
    await renderBoth();
    await renameFrom('more', 'Chat a', 'Budget');
    await waitFor(() => expect(titlesIn('more')).toEqual(['Budget', 'Chat b', 'Chat c', 'Chat d']));
    expect(titlesIn('rail')).toEqual(['Budget', 'Chat b', 'Chat c', 'Chat d']);
  });

  it('rename in the rail: More shows the new title', async () => {
    await renderBoth();
    await renameFrom('rail', 'Chat c', 'Roadmap');
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Roadmap', 'Chat d']));
    expect(titlesIn('rail')).toEqual(['Chat a', 'Chat b', 'Roadmap', 'Chat d']);
  });

  it('a failed rename keeps the old title in both', async () => {
    api.renameConversation.mockRejectedValue(new Error('500'));
    await renderBoth();
    await renameFrom('more', 'Chat a', 'Budget');
    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat c', 'Chat d']);
    expect(titlesIn('rail')).toEqual(['Chat a', 'Chat b', 'Chat c', 'Chat d']);
  });

  it('pin in the rail moves the chat into Pinned and out of Your chats, in the rail and its open More; unpin puts it back in both', async () => {
    await renderBoth();
    await choose('rail', 'Chat c', 'Pin');
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat d']));
    expect(railSection('Pinned chats')).toEqual(['Chat c']);
    expect(railSection('Your chats')).toEqual(['Chat a', 'Chat b', 'Chat d']);

    await choose('rail', 'Chat c', 'Unpin');
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat c', 'Chat d']));
    expect(railSection('Pinned chats')).toEqual([]);
    expect(within(screen.getByTestId('rail')).queryByText('Pinned chats')).toBeNull();
    expect(railSection('Your chats')).toEqual(['Chat a', 'Chat b', 'Chat c', 'Chat d']);
    expect(api.setConversationPinned.mock.calls).toEqual([
      ['c', true],
      ['c', false],
    ]);
  });

  it('pin in More takes the chat out of that panel and into the rail’s Pinned; unpin there puts it back in its place', async () => {
    await renderBoth();
    await choose('more', 'Chat d', 'Pin');
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat c']));
    expect(railSection('Pinned chats')).toEqual(['Chat d']);
    expect(railSection('Your chats')).toEqual(['Chat a', 'Chat b', 'Chat c']);

    await choose('rail', 'Chat d', 'Unpin');
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat c', 'Chat d']));
    expect(railSection('Your chats')).toEqual(['Chat a', 'Chat b', 'Chat c', 'Chat d']);
  });

  it('Your chats and Shared chats in More leave pinned chats out, with no labels: they are in Pinned', async () => {
    api.fetchConversations.mockResolvedValue({
      conversations: [{ ...ALL[0], isPinned: true }, ...ALL.slice(1)],
      pagination: { page: 1, limit: 20, totalCount: 4, totalPages: 1, hasNextPage: false, hasPrevPage: false },
    });
    const view = renderInTheme(<div data-testid="more"><MoreChatsSidebar sectionType="your" onBack={vi.fn()} /></div>);
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat b', 'Chat c', 'Chat d']));
    expect(screen.queryByText('Pinned chats')).toBeNull();
    expect(screen.queryByText('Recent')).toBeNull();
    view.unmount();

    renderInTheme(<div data-testid="more"><MoreChatsSidebar sectionType="shared" onBack={vi.fn()} /></div>);
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat b', 'Chat c', 'Chat d']));
  });
});

describe('More chats: the Pinned panel', () => {
  const listed = (conversations: Conversation[], hasNextPage = false) => ({
    conversations,
    pagination: { page: 1, limit: 20, totalCount: conversations.length, totalPages: 1, hasNextPage, hasPrevPage: false },
  });
  const pin = (c: Conversation): Conversation => ({ ...c, isPinned: true });
  const theirs = (id: string, lastActivityAt: number, pinned = false): Conversation => ({
    ...chat(id), isShared: true, isOwner: false, lastActivityAt, ...(pinned ? { isPinned: true } : {}),
  });
  type Opts = { source: 'owned' | 'shared'; search?: string };

  function Pinned() {
    return (
      <>
        <div data-testid="rail">
          <ChatSections onOpenMoreChats={vi.fn()} />
        </div>
        <div data-testid="more">
          <MoreChatsSidebar sectionType="pinned" onBack={vi.fn()} />
        </div>
      </>
    );
  }

  beforeEach(() => {
    useChatStore.setState({ moreChatsSectionType: 'pinned', sharedConversations: [] });
  });

  it('lists every pinned chat of both lists, newest activity first, under the title Pinned, with no labels', async () => {
    api.fetchConversations.mockImplementation(async (_page: number, _limit: number, opts: Opts) =>
      opts.source === 'owned'
        ? listed([pin(ALL[1]), pin(ALL[3]), ALL[0], ALL[2]])
        : listed([theirs('s1', 3500, true), theirs('s2', 9000)]),
    );
    renderInTheme(<Pinned />);

    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat s1', 'Chat b', 'Chat d']));
    expect(within(screen.getByTestId('more')).getByText('Pinned chats')).toBeTruthy();
    expect(screen.getByTestId('more').querySelector('[data-sidebar-sub-label]')).toBeNull();
    expect(api.fetchConversations.mock.calls.map(([, , opts]) => (opts as Opts).source).sort()).toEqual(['owned', 'shared']);
  });

  it('reads a list’s next page while every chat on the page is pinned, and stops at the first other one', async () => {
    const owned = (n: number) =>
      n === 1 ? listed([pin(ALL[0]), pin(ALL[1])], true) : listed([pin(ALL[2]), ALL[3]], true);
    api.fetchConversations.mockImplementation(async (n: number, _limit: number, opts: Opts) =>
      opts.source === 'owned' ? owned(n) : listed([]),
    );
    renderInTheme(<Pinned />);

    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat b', 'Chat c']));
    const ownedPages = api.fetchConversations.mock.calls.filter(([, , opts]) => (opts as Opts).source === 'owned').map(([n]) => n);
    expect(ownedPages).toEqual([1, 2]);
  });

  it('searches both lists and shows only their pinned matches', async () => {
    api.fetchConversations.mockImplementation(async (_page: number, _limit: number, opts: Opts) => {
      if (opts.search === 'plan') return opts.source === 'owned' ? listed([pin(ALL[2]), ALL[0]]) : listed([theirs('s1', 3500, true)]);
      return opts.source === 'owned' ? listed([pin(ALL[1]), pin(ALL[2])]) : listed([theirs('s1', 3500, true)]);
    });
    renderInTheme(<Pinned />);
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat s1', 'Chat b', 'Chat c']));

    fireEvent.change(screen.getByRole('textbox', { name: 'Search chats' }), { target: { value: 'plan' } });
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat s1', 'Chat c']));
    expect(api.fetchConversations.mock.calls.filter(([, , opts]) => (opts as Opts).search === 'plan')).toHaveLength(2);
  });

  it('unpin in the panel takes the chat out of it at once, back to Your chats in the rail', async () => {
    const rows = [pin(ALL[1]), pin(ALL[3]), ALL[0], ALL[2]];
    useChatStore.setState({ conversations: rows });
    api.fetchConversations.mockImplementation(async (_page: number, _limit: number, opts: Opts) =>
      opts.source === 'owned' ? listed(rows) : listed([]),
    );
    renderInTheme(<Pinned />);
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat b', 'Chat d']));
    expect(railSection('Pinned chats')).toEqual(['Chat b', 'Chat d']);

    await choose('more', 'Chat b', 'Unpin');

    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat d']));
    expect(railSection('Pinned chats')).toEqual(['Chat d']);
    expect(railSection('Your chats')).toEqual(['Chat a', 'Chat b', 'Chat c']);
  });

  it('an unpin the server refuses puts the chat back in the panel and in Pinned', async () => {
    api.setConversationPinned.mockRejectedValue(new Error('500'));
    const rows = [pin(ALL[1]), pin(ALL[3]), ALL[0], ALL[2]];
    useChatStore.setState({ conversations: rows });
    api.fetchConversations.mockImplementation(async (_page: number, _limit: number, opts: Opts) =>
      opts.source === 'owned' ? listed(rows) : listed([]),
    );
    renderInTheme(<Pinned />);
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat b', 'Chat d']));

    await choose('more', 'Chat b', 'Unpin');

    await waitFor(() => expect(toasts().map((t) => t.title)).toEqual(['Could not unpin the chat']));
    expect(titlesIn('more')).toEqual(['Chat b', 'Chat d']);
    expect(railSection('Pinned chats')).toEqual(['Chat b', 'Chat d']);
  });

  it('a chat pinned in the rail while the panel is open joins it', async () => {
    api.fetchConversations.mockImplementation(async (_page: number, _limit: number, opts: Opts) =>
      opts.source === 'owned' ? listed([pin(ALL[3]), ALL[0], ALL[1], ALL[2]]) : listed([]),
    );
    useChatStore.setState({ conversations: [pin(ALL[3]), ALL[0], ALL[1], ALL[2]] });
    renderInTheme(<Pinned />);
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat d']));

    await choose('rail', 'Chat b', 'Pin');

    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat b', 'Chat d']));
    expect(railSection('Pinned chats')).toEqual(['Chat b', 'Chat d']);
  });

  it('a new message in a pinned chat puts it first in the open panel at once, as in the rail', async () => {
    const rows = [pin(ALL[1]), pin(ALL[3]), ALL[0], ALL[2]];
    useChatStore.setState({ conversations: rows, sharedConversations: [theirs('s1', 3500, true)] });
    api.fetchConversations.mockImplementation(async (_page: number, _limit: number, opts: Opts) =>
      opts.source === 'owned' ? listed(rows) : listed([theirs('s1', 3500, true)]),
    );
    renderInTheme(<Pinned />);
    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat s1', 'Chat b', 'Chat d']));

    // What the end of an answer does: the chat moves up in the list it is in, with the time the server gave it.
    act(() => useChatStore.getState().moveConversationToTop('d', { lastActivityAt: 9000 }));

    expect(titlesIn('more')).toEqual(['Chat d', 'Chat s1', 'Chat b']);
    expect(railSection('Pinned chats')).toEqual(['Chat d', 'Chat s1', 'Chat b']);
  });

  it('its rows wear the pin right after their title, as in the rail', async () => {
    api.fetchConversations.mockImplementation(async (_page: number, _limit: number, opts: Opts) =>
      opts.source === 'owned' ? listed([pin(ALL[0]), ALL[1]]) : listed([theirs('s1', 3500, true)]),
    );
    renderInTheme(<div data-testid="more"><MoreChatsSidebar sectionType="pinned" onBack={vi.fn()} /></div>);

    await waitFor(() => expect(titlesIn('more')).toEqual(['Chat a', 'Chat s1']));
    for (const title of ['Chat a', 'Chat s1']) {
      expect(within(screen.getByTestId('more')).getByText(title).nextElementSibling?.hasAttribute('data-pinned-mark')).toBe(true);
    }
  });

  it('says what fills it when nothing is pinned', async () => {
    api.fetchConversations.mockResolvedValue(listed(ALL));
    renderInTheme(<div data-testid="more"><MoreChatsSidebar sectionType="pinned" onBack={vi.fn()} /></div>);

    await waitFor(() => expect(screen.getByText('Chats you pin will appear here')).toBeTruthy());
  });
});

describe('More chats load-more failure', () => {
  /** A stand-in for the browser's observer: the test says when the sentinel comes into range. */
  const observers: Array<{ fire: () => void; live: boolean }> = [];
  class FakeIntersectionObserver {
    private entry: { live: boolean; fire: () => void };
    constructor(callback: IntersectionObserverCallback) {
      this.entry = {
        live: true,
        fire: () => callback([{ isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver),
      };
      observers.push(this.entry);
    }
    observe() {}
    unobserve() {}
    disconnect() {
      this.entry.live = false;
    }
  }
  const fireInRange = () => act(() => observers.filter((o) => o.live).at(-1)?.fire());
  const page = (ids: string[], n: number, hasNextPage: boolean) => ({
    conversations: ids.map((id) => chat(id)),
    pagination: { page: n, limit: 20, totalCount: 40, totalPages: 2, hasNextPage, hasPrevPage: n > 1 },
  });
  const moreTitles = () => within(screen.getByTestId('more')).queryAllByRole('link').map(rowText);

  beforeEach(() => {
    observers.length = 0;
    vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('asks for the next page without the global error toast; on failure the rows stay, and a scroll asks again', async () => {
    api.fetchConversations.mockImplementation((n: number) =>
      n === 1 ? Promise.resolve(page(['p1', 'p2'], 1, true)) : Promise.reject(new Error('500')),
    );
    renderInTheme(
      <div data-testid="more">
        <MoreChatsSidebar sectionType="your" onBack={vi.fn()} />
      </div>,
    );
    await waitFor(() => expect(moreTitles()).toEqual(['Chat p1', 'Chat p2']));

    await fireInRange();
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(2));
    expect(api.fetchConversations.mock.calls[1][0]).toBe(2);
    expect(api.fetchConversations.mock.calls[1][2]).toMatchObject({ suppressErrorToast: true });
    await waitFor(() => expect(useChatStore.getState().moreChats.isLoadingMore).toBe(false));
    expect(moreTitles()).toEqual(['Chat p1', 'Chat p2']);
    expect(toasts()).toHaveLength(0);

    // A failing server is not polled: the observer coming round again asks nothing until the list scrolls.
    await fireInRange();
    expect(api.fetchConversations).toHaveBeenCalledTimes(2);

    api.fetchConversations.mockImplementation(() => Promise.resolve(page(['p3'], 2, false)));
    const list = within(screen.getByTestId('more')).getByRole('textbox').closest('.rt-TextFieldRoot')!.nextElementSibling!;
    await act(async () => {
      fireEvent.scroll(list);
    });
    await waitFor(() => expect(moreTitles()).toEqual(['Chat p1', 'Chat p2', 'Chat p3']));
    expect(api.fetchConversations).toHaveBeenCalledTimes(3);
  });

  it('a list too short to scroll retries on a wheel or swipe over it', async () => {
    let fail = true;
    api.fetchConversations.mockImplementation((n: number) =>
      n === 1 ? Promise.resolve(page(['p1'], 1, true)) : fail ? Promise.reject(new Error('500')) : Promise.resolve(page(['p2'], 2, false)),
    );
    renderInTheme(
      <div data-testid="more">
        <MoreChatsSidebar sectionType="your" onBack={vi.fn()} />
      </div>,
    );
    await waitFor(() => expect(moreTitles()).toEqual(['Chat p1']));
    await fireInRange();
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(useChatStore.getState().moreChats.isLoadingMore).toBe(false));

    fail = false;
    const list = within(screen.getByTestId('more')).getByRole('textbox').closest('.rt-TextFieldRoot')!.nextElementSibling!;
    await act(async () => {
      fireEvent.wheel(list, { deltaY: 40 });
    });
    await waitFor(() => expect(moreTitles()).toEqual(['Chat p1', 'Chat p2']));
  });

  it('the first page still goes through the shared error toast (a list that failed to load is not "No chats yet" in silence)', async () => {
    renderInTheme(
      <div data-testid="more">
        <MoreChatsSidebar sectionType="your" onBack={vi.fn()} />
      </div>,
    );
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalled());
    expect(api.fetchConversations.mock.calls[0][2]).not.toHaveProperty('suppressErrorToast');
  });
});
