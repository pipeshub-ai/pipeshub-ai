/**
 * The More chats panel's search lives in the store: the panel shown again (the drawer reopened, a view swapped back)
 * keeps what was typed and the rows found for it, and closing the panel clears it.
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

const api = vi.hoisted(() => ({ fetchConversations: vi.fn() }));
vi.mock('@/chat/api', () => ({ ChatApi: api }));

const { useChatStore } = await import('@/chat/store');
const { MoreChatsSidebar } = await import('../more-chats-sidebar');

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

const renderMore = () =>
  renderInTheme(
    <div data-testid="more">
      <MoreChatsSidebar sectionType="your" onBack={vi.fn()} />
    </div>,
  );

beforeEach(() => {
  installBrowserShims();
  api.fetchConversations.mockReset();
  api.fetchConversations.mockImplementation(async (_p: number, _l: number, opts: { search?: string }) =>
    opts.search === 'budget' ? page(['b']) : page(['a', 'b', 'c']),
  );
  useChatStore.setState({ ...initialState, isMoreChatsPanelOpen: true, moreChatsSectionType: 'your' });
});
afterEach(() => cleanup());

describe('More chats search', () => {
  it('shown again, the panel keeps the search and its results, and asks for nothing', async () => {
    const first = renderMore();
    await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b', 'Chat c']));
    fireEvent.change(screen.getByRole('textbox', { name: 'Search chats' }), { target: { value: 'budget' } });
    await waitFor(() => expect(titles()).toEqual(['Chat b']));
    const asked = api.fetchConversations.mock.calls.length;
    first.unmount();

    renderMore();

    expect((screen.getByRole('textbox', { name: 'Search chats' }) as HTMLInputElement).value).toBe('budget');
    expect(titles()).toEqual(['Chat b']);
    expect(api.fetchConversations).toHaveBeenCalledTimes(asked);
  });

  it('closing the panel clears the search, so the next open starts empty', () => {
    act(() => useChatStore.getState().setMoreChatsSearch('budget'));

    act(() => useChatStore.getState().closeMoreChatsPanel());

    expect(useChatStore.getState().moreChatsSearch).toBe('');
  });
});
