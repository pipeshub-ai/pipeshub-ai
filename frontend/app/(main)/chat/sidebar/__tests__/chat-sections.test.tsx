/**
 * The sidebar's two chat groups, Shared chats and Your chats, show on load with no Recents toggle. Your chats files
 * its rows under the day of their last activity; Shared chats is one list. Each shows at most five rows, then More.
 * An edition can leave rows out of both lists.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { getI18n } from 'react-i18next';
import type { Conversation } from '@/chat/types';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
const drawn = vi.hoisted(() => ({ rows: 0 }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../chat-section-element', () => ({
  ChatSectionElement: ({ conversation, onClick }: { conversation: Conversation; onClick?: () => void }) => {
    drawn.rows += 1;
    return <div data-testid="row" onClick={onClick}>{conversation.title}</div>;
  },
  GeneratingTitleItem: () => <div data-testid="row">Generating</div>,
  StartChatButton: () => null,
  ChatItemSkeleton: () => null,
}));

import { chatSlots } from '@/config';
import { useChatStore } from '@/chat/store';
import { ChatSections } from '../chat-sections';
import { ChatSection } from '../chat-section';

// The section's name comes from the translations, which an edition may word differently.
const SHARED = () => getI18n().t('chat.sharedChats');

const DAY = 24 * 60 * 60 * 1000;
const chat = (id: string, title: string, lastActivityAt: number) =>
  ({ id, title, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z', isShared: false, sharedWith: [], lastActivityAt }) as unknown as Conversation;
const rows = () => screen.queryAllByTestId('row').map((row) => row.textContent);
/** Day labels (`~Today`) and rows, top to bottom. */
const daysAndRows = () =>
  Array.from(document.querySelectorAll('[data-sidebar-sub-label], [data-testid="row"]')).map((el) =>
    el.hasAttribute('data-sidebar-sub-label') ? `~${el.textContent ?? ''}` : el.textContent,
  );

beforeEach(() => {
  useChatStore.getState().reset();
  const now = Date.now();
  useChatStore.setState({
    sharedConversations: [chat('s1', 'Shared plan', now)],
    conversations: [chat('c1', 'Today chat', now), chat('c2', 'Old chat', now - 30 * DAY)],
    isConversationsLoading: false,
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const page = (hasNextPage: boolean) => ({ page: 1, limit: 10, totalCount: 0, totalPages: 1, hasNextPage, hasPrevPage: false });
const many = (prefix: string, n: number) =>
  Array.from({ length: n }, (_, i) => chat(`${prefix}${String(i + 1)}`, `${prefix}${String(i + 1)}`, Date.now() - i * DAY));

describe('ChatSections', () => {
  it('shows both groups on load, with no Recents toggle and no expand state; Your chats by day, Shared chats not', () => {
    const { container } = render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(screen.queryByText('Recents')).toBeNull();
    expect(container.querySelector('[aria-expanded]')).toBeNull();
    expect(screen.getByText(SHARED())).toBeTruthy();
    expect(screen.getByText('Your chats')).toBeTruthy();
    // Only the days that have a chat, and no Pinned with nothing pinned.
    for (const label of ['Yesterday', 'Last 7 days', 'Pinned']) {
      expect(screen.queryByText(label)).toBeNull();
    }
    expect(daysAndRows()).toEqual(['Shared plan', '~Today', 'Today chat', '~Older', 'Old chat']);
  });

  it('shows the shared list skeleton while the lists load', () => {
    useChatStore.setState({ isConversationsLoading: true });
    const { container } = render(<ChatSections onOpenMoreChats={() => {}} />);
    const skeletons = container.querySelectorAll('[data-list-skeleton="sidebar-row"]');
    expect(skeletons).toHaveLength(2);
    skeletons.forEach((el) => expect(el.getAttribute('aria-busy')).toBe('true'));
  });

  it('leaves out the rows the edition hides, in both lists', () => {
    vi.spyOn(chatSlots, 'useVisibleConversations').mockImplementation((rows) => rows.filter((row) => row.id !== 's1' && row.id !== 'c2'));
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(rows()).toEqual(['Today chat']);
  });

  it('caps each group at five rows, then one More row that opens that group in More', () => {
    const onOpenMoreChats = vi.fn();
    useChatStore.setState({ sharedConversations: many('s', 7), conversations: many('c', 8) });
    render(<ChatSections onOpenMoreChats={onOpenMoreChats} />);
    expect(rows()).toEqual(['s1', 's2', 's3', 's4', 's5', 'c1', 'c2', 'c3', 'c4', 'c5']);
    const more = screen.getAllByText('More');
    expect(more).toHaveLength(2);
    fireEvent.click(more[0]);
    expect(onOpenMoreChats).toHaveBeenLastCalledWith('shared');
    fireEvent.click(more[1]);
    expect(onOpenMoreChats).toHaveBeenLastCalledWith('your');
  });

  it('shows More when the server has another page, even with five or fewer rows', () => {
    useChatStore.setState({ sharedConversations: many('s', 3), sharedPagination: page(true), conversations: many('c', 2), pagination: page(false) });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(rows()).toEqual(['s1', 's2', 's3', 'c1', 'c2']);
    expect(screen.getAllByText('More')).toHaveLength(1);
  });

  it('with five or fewer chats and no next page, shows them all and no More', () => {
    useChatStore.setState({ sharedConversations: many('s', 5), conversations: many('c', 5) });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(rows()).toHaveLength(10);
    expect(screen.queryByText('More')).toBeNull();
  });

  it('a removed chat lets the sixth one move up, with no reload', () => {
    useChatStore.setState({ sharedConversations: [], conversations: many('c', 7) });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(rows()).toEqual(['c1', 'c2', 'c3', 'c4', 'c5']);
    act(() => useChatStore.getState().removeConversation('c2'));
    expect(rows()).toEqual(['c1', 'c3', 'c4', 'c5', 'c6']);
  });

  it('hides Shared chats entirely when there is none', () => {
    useChatStore.setState({ sharedConversations: [] });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(screen.queryByText(SHARED())).toBeNull();
    expect(screen.queryByText('No shared chats yet')).toBeNull();
    expect(screen.getByText('Your chats')).toBeTruthy();
  });

  it('with no chats of your own, says the one line "Your chats will appear here" and shows no More', () => {
    useChatStore.setState({ sharedConversations: [], conversations: [] });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(screen.getByText('Your chats will appear here')).toBeTruthy();
    expect(screen.queryByText('More')).toBeNull();
  });

  it("puts a section's empty line in line with its label", () => {
    useChatStore.setState({ sharedConversations: [], conversations: [] });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    // The heading row's padding sets where its label starts; the line's own padding sets where it starts.
    const startOf = (el: HTMLElement) => el.style.padding.split(' ').at(-1);
    const label = screen.getByText('Your chats').parentElement as HTMLElement;
    const line = screen.getByText('Your chats will appear here');
    expect(startOf(label)).toBe('var(--space-3)');
    expect(startOf(line)).toBe(startOf(label));
  });

  it('Your chats has a New chat + that starts a chat; Shared chats has none, and titles are plain text', () => {
    render(<ChatSections onOpenMoreChats={() => {}} />);
    const add = screen.getAllByRole('button', { name: 'New chat' });
    expect(add).toHaveLength(1);
    expect(add[0].classList.contains('touch-hit')).toBe(true);
    fireEvent.click(add[0]);
    expect(router.push).toHaveBeenCalledWith('/chat/');
    expect((screen.getByText(SHARED()) as HTMLElement).style.cursor).toBe('default');
  });
});

describe('Your chats by day', () => {
  // Midday, so no fixture crosses local midnight.
  const NOW = new Date(2026, 0, 15, 12, 0, 0).getTime();
  const HOUR = 60 * 60 * 1000;
  const at = (id: string, time: number) => chat(id, id, time);

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it('files the rows under Today, Yesterday, Last 7 days and Older, in that order, with only the days that have one', () => {
    useChatStore.setState({
      sharedConversations: [at('s1', NOW - 3 * DAY)],
      conversations: [
        at('today', NOW - HOUR),
        at('yesterday', NOW - DAY),
        at('this week', NOW - 3 * DAY),
        at('last month', NOW - 30 * DAY),
      ],
    });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(daysAndRows()).toEqual([
      's1',
      '~Today', 'today', '~Yesterday', 'yesterday', '~Last 7 days', 'this week', '~Older', 'last month',
    ]);
    cleanup();

    useChatStore.setState({ sharedConversations: [], conversations: [at('yesterday', NOW - DAY), at('old', NOW - 9 * DAY)] });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(daysAndRows()).toEqual(['~Yesterday', 'yesterday', '~Older', 'old']);
  });

  it('caps the rows at five across the days, labels aside, then More', () => {
    useChatStore.setState({ sharedConversations: [], conversations: many('c', 8) });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(daysAndRows()).toEqual(['~Today', 'c1', '~Yesterday', 'c2', '~Last 7 days', 'c3', 'c4', 'c5']);
    expect(screen.getAllByText('More')).toHaveLength(1);
  });

  it('a new message puts the chat under Today at once, first', () => {
    useChatStore.setState({ sharedConversations: [], conversations: [at('t1', NOW - 2 * HOUR), at('y1', NOW - DAY)] });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(daysAndRows()).toEqual(['~Today', 't1', '~Yesterday', 'y1']);

    // What the end of an answer does: the chat moves up with the time the server saved for it.
    act(() => useChatStore.getState().moveConversationToTop('y1', { lastActivityAt: NOW }));
    expect(daysAndRows()).toEqual(['~Today', 'y1', 't1']);
  });

  it('a new message in the newest chat of all, from an earlier day, puts it under Today too', () => {
    useChatStore.setState({ sharedConversations: [], conversations: [at('y1', NOW - DAY), at('o1', NOW - 20 * DAY)] });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(daysAndRows()).toEqual(['~Yesterday', 'y1', '~Older', 'o1']);

    act(() => useChatStore.getState().moveConversationToTop('y1', { lastActivityAt: NOW }));
    expect(daysAndRows()).toEqual(['~Today', 'y1', '~Older', 'o1']);
  });

  it('a chat still being written shows under Today, even when no other chat is from today', () => {
    useChatStore.setState({ sharedConversations: [], conversations: [at('y1', NOW - DAY)] });
    useChatStore.getState().addPendingConversation(useChatStore.getState().createSlot(null));
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(daysAndRows()).toEqual(['~Today', 'Generating', '~Yesterday', 'y1']);
  });

  it('a rail left open past midnight files its rows under the new day, with no reload', () => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
    const lateEvening = new Date(2026, 0, 15, 23, 59, 30).getTime();
    vi.setSystemTime(lateEvening);
    useChatStore.setState({
      sharedConversations: [],
      conversations: [at('evening', lateEvening - HOUR), at('monday', lateEvening - 2 * DAY)],
    });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(daysAndRows()).toEqual(['~Today', 'evening', '~Last 7 days', 'monday']);

    act(() => vi.advanceTimersByTime(60_000));
    expect(daysAndRows()).toEqual(['~Yesterday', 'evening', '~Last 7 days', 'monday']);
  });
});

describe('ChatSections while an answer streams', () => {
  /** A new chat whose answer streams: its slot, and its "Generating title…" row. */
  function streamingNewChat() {
    let id = '';
    act(() => {
      id = useChatStore.getState().createSlot(null);
      useChatStore.getState().updateSlot(id, { isStreaming: true });
      useChatStore.getState().addPendingConversation(id);
    });
    return id;
  }
  /** An answer streams: its slot changes many times a second. */
  const stream = (id: string, updates = 20) => {
    for (let i = 1; i <= updates; i += 1) {
      act(() => useChatStore.getState().updateSlot(id, { streamingContent: 'word '.repeat(i) }));
    }
  };

  it('does not draw its rows again for each streaming update, and still shows a new chat while its title comes', () => {
    render(<ChatSections onOpenMoreChats={() => {}} />);
    const id = streamingNewChat();
    expect(rows()).toContain('Generating');
    const before = drawn.rows;
    stream(id);
    expect(drawn.rows).toBe(before);
    act(() => useChatStore.getState().clearPendingConversation(id));
    expect(rows()).not.toContain('Generating');
  });

  it('is not drawn again itself for each streaming update (only when its rows change)', () => {
    let commits = 0;
    render(
      <React.Profiler id="rail" onRender={() => { commits += 1; }}>
        <ChatSections onOpenMoreChats={() => {}} />
      </React.Profiler>,
    );
    const id = streamingNewChat();
    const before = commits;
    stream(id);
    expect(commits).toBe(before);
  });

  it('draws only the row of a chat that joins the list, not every row again', () => {
    useChatStore.setState({ sharedConversations: [], conversations: many('c', 4) });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(rows()).toEqual(['c1', 'c2', 'c3', 'c4']);
    const before = drawn.rows;
    // A run ends: its chat joins the top of the list.
    act(() => useChatStore.setState({ conversations: [chat('new', 'New chat', Date.now()), ...useChatStore.getState().conversations] }));
    expect(rows()).toEqual(['New chat', 'c1', 'c2', 'c3', 'c4']);
    expect(drawn.rows - before).toBe(1);
  });

  it("sends a row's click to the list's handler as it is now", () => {
    const first = vi.fn();
    const now = vi.fn();
    // The same chats in both draws: their rows are not drawn again.
    const chats = many('c', 2);
    const list = (onSelectConversation: (id: string) => void) => (
      <ChatSection
        variant="rail"
        title="Shared chats"
        conversations={chats}
        isLoading={false}
        hasError={false}
        currentConversationId={null}
        onSelectConversation={onSelectConversation}
        onNewChat={() => {}}
        skeletonCount={1}
      />
    );
    const { rerender } = render(list(first));
    rerender(list(now));
    fireEvent.click(screen.getAllByTestId('row')[1]);
    expect(now).toHaveBeenCalledWith('c2');
    expect(first).not.toHaveBeenCalled();
  });
});
