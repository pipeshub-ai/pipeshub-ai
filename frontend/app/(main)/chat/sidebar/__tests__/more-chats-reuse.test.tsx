/**
 * The More chats panel shown again (the drawer reopened, the rail mounted again) keeps the rows it listed: no
 * skeleton and no request while they are fresh, a quiet reload of page 1 once they are stale.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, screen, waitFor, within } from '@testing-library/react';
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

const api = vi.hoisted(() => ({ fetchConversations: vi.fn() }));
vi.mock('@/chat/api', () => ({ ChatApi: api }));

const { useChatStore } = await import('@/chat/store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { MoreChatsSidebar } = await import('../more-chats-sidebar');
const { SIDEBAR_LISTS_STALE_MS } = await import('../../constants');

const initialState = useChatStore.getState();

const chat = (id: string): Conversation => ({
  id,
  title: `Chat ${id}`,
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
  isShared: false,
  sharedWith: [],
  lastActivityAt: 1000,
  isOwner: true,
});

const page = (ids: string[]) => ({
  conversations: ids.map(chat),
  pagination: { page: 1, limit: 20, totalCount: ids.length, totalPages: 1, hasNextPage: false, hasPrevPage: false },
});

const titles = () =>
  within(screen.getByTestId('more'))
    .queryAllByRole('link')
    .map((a) => (a.textContent ?? '').trim());

const renderMore = (sectionType: 'your' | 'pinned' = 'your') =>
  renderInTheme(
    <div data-testid="more">
      <MoreChatsSidebar sectionType={sectionType} onBack={vi.fn()} />
    </div>,
  );

beforeEach(() => {
  installBrowserShims();
  api.fetchConversations.mockReset();
  api.fetchConversations.mockResolvedValue(page(['a', 'b']));
  useChatStore.setState({ ...initialState, isMoreChatsPanelOpen: true, moreChatsSectionType: 'your' });
  useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: false } } as never);
});
afterEach(() => cleanup());

describe('More chats shown again', () => {
  it('within 30 s: the same rows at once, and no request', async () => {
    const first = renderMore();
    await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b']));
    first.unmount();

    renderMore();

    expect(titles()).toEqual(['Chat a', 'Chat b']);
    expect(screen.queryByRole('status')).toBeNull();
    expect(api.fetchConversations).toHaveBeenCalledTimes(1);
  });

  it('once stale: page 1 loads again in the background, and the rows stay until it lands', async () => {
    const first = renderMore();
    await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b']));
    first.unmount();
    useChatStore.setState((s) => ({ moreChats: { ...s.moreChats, loadedAt: Date.now() - SIDEBAR_LISTS_STALE_MS - 1 } }));
    let answer: (value: unknown) => void = () => {};
    api.fetchConversations.mockReturnValue(new Promise((resolve) => (answer = resolve)));

    renderMore();

    expect(titles()).toEqual(['Chat a', 'Chat b']);
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(2));
    expect(titles()).toEqual(['Chat a', 'Chat b']);
    await act(async () => answer(page(['c', 'a'])));
    expect(titles()).toEqual(['Chat c', 'Chat a']);
  });

  it('stale with later pages loaded: the rows stay as they are, so no page goes missing between them', async () => {
    const first = renderMore();
    await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b']));
    first.unmount();
    useChatStore.setState((s) => ({
      moreChats: { ...s.moreChats, page: 2, loadedAt: Date.now() - SIDEBAR_LISTS_STALE_MS - 1 },
    }));

    renderMore();

    expect(titles()).toEqual(['Chat a', 'Chat b']);
    expect(api.fetchConversations).toHaveBeenCalledTimes(1);
  });

  it('a stale reload that fails keeps the rows', async () => {
    const first = renderMore();
    await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b']));
    first.unmount();
    useChatStore.setState((s) => ({ moreChats: { ...s.moreChats, loadedAt: Date.now() - SIDEBAR_LISTS_STALE_MS - 1 } }));
    api.fetchConversations.mockRejectedValue(new Error('500'));

    renderMore();

    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(2));
    await act(async () => {});
    expect(titles()).toEqual(['Chat a', 'Chat b']);
  });

  it('Pinned loads again when Projects is turned on, since it then leaves project chats out', async () => {
    useChatStore.setState({ moreChatsSectionType: 'pinned' });
    api.fetchConversations.mockResolvedValue(page([]));
    renderMore('pinned');
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(2));

    act(() => useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as never));

    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(4));
    expect(api.fetchConversations).toHaveBeenLastCalledWith(1, expect.any(Number), expect.objectContaining({ excludeProjectChats: false }));
    expect(api.fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), expect.objectContaining({ source: 'owned', excludeProjectChats: true }));
  });

  it('opened again from the rail: a new list, loaded afresh', async () => {
    const first = renderMore();
    await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b']));
    first.unmount();
    act(() => {
      useChatStore.getState().closeMoreChatsPanel();
      useChatStore.getState().toggleMoreChatsPanel('your');
    });
    api.fetchConversations.mockResolvedValue(page(['d']));

    renderMore();

    await waitFor(() => expect(titles()).toEqual(['Chat d']));
    expect(api.fetchConversations).toHaveBeenCalledTimes(2);
  });
});
