/**
 * A change made from a rail row changes that row and reloads no list: rename and archive at once (put back if the
 * server refuses), Undo puts the saved row back, delete takes the row once its confirm succeeds, pin moves it.
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
const { ChatSections } = await import('../chat-sections');
const { useSidebarConversations, resetSidebarConversationsForTests, loadSidebarConversations } = await import(
  '../use-sidebar-conversations'
);

const initialState = useChatStore.getState();

const chat = (id: string, lastActivityAt: number): Conversation => ({
  id,
  title: `Chat ${id}`,
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
  isShared: false,
  sharedWith: [],
  lastActivityAt,
  isOwner: true,
});
const ALL = ['a', 'b', 'c'].map((id, i) => chat(id, 3000 - i * 1000));

/** The rail as a page mounts it: its lists, kept fresh by the loader. */
function Rail() {
  useSidebarConversations();
  return <ChatSections onOpenMoreChats={vi.fn()} />;
}

const titles = () => screen.queryAllByRole('link').map((a) => (a.textContent ?? '').replace(', pinned', '').trim());
const toasts = () => useToastStore.getState().toasts.filter((t) => !t.isExiting);

async function choose(title: string, item: string) {
  const row = screen.getByText(title).closest('a')!.parentElement as HTMLElement;
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

beforeEach(async () => {
  installBrowserShims();
  resetSidebarConversationsForTests();
  useToastStore.setState({ toasts: [] });
  Object.values(api).forEach((fn) => {
    fn.mockReset();
    fn.mockResolvedValue(undefined);
  });
  api.fetchConversations.mockImplementation(async (_p: number, _l: number, opts: { source: string }) => ({
    conversations: opts.source === 'owned' ? ALL : [],
    pagination: { page: 1, limit: 10, totalCount: 3, totalPages: 1, hasNextPage: false, hasPrevPage: false },
  }));
  useChatStore.setState({ ...initialState });
  // Lists loaded a moment ago: the rail asks nothing when it mounts.
  await loadSidebarConversations();
  api.fetchConversations.mockClear();
});
afterEach(() => cleanup());

/** What the rail asked the server for: after a change made from a row, nothing. */
const listReads = () => api.fetchConversations.mock.calls.length;

describe('rail row changes reload nothing', () => {
  it('rename: the new title at once, and no list read', async () => {
    let answer: () => void = () => {};
    api.renameConversation.mockReturnValue(new Promise<void>((resolve) => (answer = resolve)));
    renderInTheme(<Rail />);
    await choose('Chat b', 'Rename');
    const field = await screen.findByRole('textbox', { name: 'New name' });
    fireEvent.change(field, { target: { value: 'Budget' } });
    await act(async () => {
      fireEvent.keyDown(field, { key: 'Enter' });
      field.blur();
    });

    expect(titles()).toEqual(['Chat a', 'Budget', 'Chat c']);
    await act(async () => answer());
    expect(titles()).toEqual(['Chat a', 'Budget', 'Chat c']);
    expect(listReads()).toBe(0);
  });

  it('a refused rename puts the old title back and says so', async () => {
    api.renameConversation.mockRejectedValue(new Error('500'));
    renderInTheme(<Rail />);
    await choose('Chat b', 'Rename');
    const field = await screen.findByRole('textbox', { name: 'New name' });
    fireEvent.change(field, { target: { value: 'Budget' } });
    await act(async () => {
      fireEvent.keyDown(field, { key: 'Enter' });
      field.blur();
    });

    await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b', 'Chat c']));
    expect(toasts().map((t) => t.variant)).toEqual(['error']);
  });

  it('archive: the row goes before the server answers; Undo puts it back at its place; no list read', async () => {
    let answer: () => void = () => {};
    api.archiveConversation.mockReturnValue(new Promise<void>((resolve) => (answer = resolve)));
    renderInTheme(<Rail />);
    await choose('Chat b', 'Archive');

    expect(titles()).toEqual(['Chat a', 'Chat c']);
    await act(async () => answer());
    await waitFor(() => expect(toasts()).toHaveLength(1));

    act(() => toasts()[0].action!.onClick!());

    await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b', 'Chat c']));
    expect(api.restoreConversation).toHaveBeenCalledWith('b');
    expect(listReads()).toBe(0);
  });

  it('a refused archive puts the row back where it was, with no archived toast', async () => {
    let refuse: () => void = () => {};
    api.archiveConversation.mockReturnValue(new Promise<void>((_resolve, reject) => (refuse = () => reject(new Error('500')))));
    renderInTheme(<Rail />);
    await choose('Chat b', 'Archive');
    expect(titles()).toEqual(['Chat a', 'Chat c']);

    await act(async () => refuse());

    expect(titles()).toEqual(['Chat a', 'Chat b', 'Chat c']);
    expect(toasts().find((t) => t.title === 'Chat archived')).toBeUndefined();
  });

  it('pin and delete: the row moves or goes, and no list read', async () => {
    renderInTheme(<Rail />);
    await choose('Chat c', 'Pin');
    await waitFor(() => expect(api.setConversationPinned).toHaveBeenCalledWith('c', true));
    await act(async () => {});
    expect(titles()[0]).toBe('Chat c');

    await choose('Chat a', 'Delete');
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'DELETE' } });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    });

    await waitFor(() => expect(titles()).not.toContain('Chat a'));
    expect(listReads()).toBe(0);
    expect(useChatStore.getState().conversationsVersion).toBe(0);
  });
});
