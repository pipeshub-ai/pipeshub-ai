/**
 * The rail after the user's own change, with no list reload after it: a chat pinned from More shows in the rail, and
 * a renamed chat keeps its new title over a quiet refresh that read the server before the rename was saved.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { Conversation } from '@/chat/types';

installMemoryStorage();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
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
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { ChatSections } = await import('../chat-sections');
const { MoreChatsSidebar } = await import('../more-chats-sidebar');
const { useSidebarConversations, resetSidebarConversationsForTests, loadSidebarConversations } = await import(
  '../use-sidebar-conversations'
);
const { resetHeldTitlesForTests } = await import('@/chat/utils/held-titles');
const initialState = useChatStore.getState();

/** The server's own chats, listed pinned first, then newest activity first. */
let server: Array<{ id: string; title: string; at: number; pinned?: boolean }> = [];
const asRow = (r: (typeof server)[number]): Conversation => ({
  id: r.id,
  title: r.title,
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
  isShared: false,
  sharedWith: [],
  lastActivityAt: r.at,
  isOwner: true,
  ...(r.pinned ? { isPinned: true } : {}),
});
const listed = (limit: number) =>
  [...server]
    .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned) || b.at - a.at)
    .slice(0, limit)
    .map(asRow);

function Rail() {
  useSidebarConversations();
  return (
    <div data-testid="rail">
      <ChatSections onOpenMoreChats={vi.fn()} />
    </div>
  );
}
const railTitles = () =>
  within(screen.getByTestId('rail'))
    .queryAllByRole('link')
    .map((a) => (a.textContent ?? '').replace(', pinned', '').trim());

async function choose(scope: HTMLElement, title: string, item: string) {
  const row = within(scope).getByText(title).closest('a')!.parentElement as HTMLElement;
  fireEvent.mouseEnter(row);
  const trigger = within(row).getByRole('button', { name: `Actions for ${title}` });
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('menuitem', { name: item }));
  });
}

/** Renames `title` from its rail row; the server answers when the returned function is called. */
async function renameFromRail(title: string, next: string) {
  let answer: () => void = () => {};
  api.renameConversation.mockImplementation(
    (id: string, newTitle: string) =>
      new Promise<void>((resolve) => {
        answer = () => {
          server = server.map((r) => (r.id === id ? { ...r, title: newTitle } : r));
          resolve();
        };
      }),
  );
  await choose(screen.getByTestId('rail'), title, 'Rename');
  const field = await screen.findByRole('textbox', { name: 'New name' });
  fireEvent.change(field, { target: { value: next } });
  await act(async () => {
    fireEvent.keyDown(field, { key: 'Enter' });
    field.blur();
  });
  return () => act(async () => answer());
}

/** A quiet refresh of the rail's lists starts (as on focus once they are stale, or on a notice). */
async function focusRefresh() {
  await act(async () => {
    useChatStore.getState().bumpConversationsVersion();
  });
}

const settle = () => act(async () => new Promise((resolve) => setTimeout(resolve, 30)));

beforeEach(async () => {
  installBrowserShims();
  resetSidebarConversationsForTests();
  resetHeldTitlesForTests();
  Object.values(api).forEach((fn) => {
    fn.mockReset();
    fn.mockResolvedValue(undefined);
  });
  server = [
    { id: 'a', title: 'Chat a', at: 5000 },
    { id: 'b', title: 'Chat b', at: 4000 },
    { id: 'c', title: 'Chat c', at: 3000 },
  ];
  // The rail's page here is two rows long, so c is listed only in More (which reads 20).
  api.fetchConversations.mockImplementation(async (_p: number, limit: number, opts: { source: string }) => ({
    conversations: opts.source === 'owned' ? listed(limit === 10 ? 2 : 20) : [],
    pagination: { page: 1, limit, totalCount: server.length, totalPages: 1, hasNextPage: false, hasPrevPage: false },
  }));
  api.setConversationPinned.mockImplementation(async (id: string, pinned: boolean) => {
    server = server.map((r) => (r.id === id ? { ...r, pinned } : r));
  });
  useChatStore.setState({ ...initialState });
  useFeatureFlagsStore.setState({ flags: {} } as never);
  await loadSidebarConversations();
  api.fetchConversations.mockClear();
});
afterEach(() => cleanup());

describe('a chat pinned from More that the rail does not list', () => {
  it('shows in the rail at once, with no list read', async () => {
    useChatStore.setState({ isMoreChatsPanelOpen: true, moreChatsSectionType: 'your' });
    renderInTheme(
      <>
        <Rail />
        <div data-testid="more">
          <MoreChatsSidebar sectionType="your" onBack={vi.fn()} />
        </div>
      </>,
    );
    await waitFor(() => expect(within(screen.getByTestId('more')).queryAllByText('Chat c').length).toBe(1));
    expect(railTitles()).toEqual(['Chat a', 'Chat b']);
    const readsBefore = api.fetchConversations.mock.calls.length;

    await choose(screen.getByTestId('more'), 'Chat c', 'Pin');

    expect(api.setConversationPinned).toHaveBeenCalledWith('c', true);
    expect(railTitles()).toEqual(['Chat c', 'Chat a', 'Chat b']);
    await settle();
    expect(api.fetchConversations.mock.calls.length).toBe(readsBefore);
  });
});

describe('a rename and a quiet refresh that read the old title', () => {
  it('the refresh answered first: the row keeps the new title, then and once the rename is saved', async () => {
    renderInTheme(<Rail />);
    const answerRename = await renameFromRail('Chat b', 'Budget');

    await focusRefresh();
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalled());
    await settle();
    expect(railTitles()).toContain('Budget');

    await answerRename();
    await settle();
    expect(railTitles()).toEqual(['Chat a', 'Budget']);
  });

  it('the refresh left before the save and is answered after it: the row keeps the new title', async () => {
    renderInTheme(<Rail />);
    const answerRename = await renameFromRail('Chat b', 'Budget');
    let answerRead: () => void = () => {};
    api.fetchConversations.mockImplementation((_p: number, limit: number, opts: { source: string }) => {
      // Read now, from a server that does not have the rename yet; answered later.
      const conversations = opts.source === 'owned' ? listed(2) : [];
      return new Promise((resolve) => {
        const prev = answerRead;
        answerRead = () => {
          prev();
          resolve({ conversations, pagination: { page: 1, limit, totalCount: 3, totalPages: 1, hasNextPage: false, hasPrevPage: false } });
        };
      });
    });

    await focusRefresh();
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(2));
    await answerRename();
    await act(async () => answerRead());
    await settle();

    expect(railTitles()).toEqual(['Chat a', 'Budget']);
  });
});
