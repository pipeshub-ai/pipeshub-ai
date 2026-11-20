/**
 * Archiving your own chat from its row asks nothing first: the row goes, and one toast says
 * where it went with an Undo that restores it. A rename that the server refuses says so and
 * keeps the old title.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { TOAST_DEFAULT_DURATIONS, useToastStore } from '@/lib/store/toast-store';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { Conversation } from '@/chat/types';

installMemoryStorage();

const nav = vi.hoisted(() => ({ conversationId: null as string | null }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(nav.conversationId ? `conversationId=${nav.conversationId}` : ''),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const api = vi.hoisted(() => ({
  archiveConversation: vi.fn(),
  restoreConversation: vi.fn(),
  renameConversation: vi.fn(),
}));
vi.mock('@/chat/api', () => ({ ChatApi: api }));

const { useChatStore } = await import('@/chat/store');
const { ChatSectionElement } = await import('../chat-section-element');

const initialState = useChatStore.getState();

function chat(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 'conv-1',
    title: 'Quarterly plan',
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    isShared: false,
    sharedWith: [],
    lastActivityAt: 1000,
    isOwner: true,
    ...overrides,
  };
}

function StoreRow() {
  const conversation = useChatStore((s) => s.conversations.find((c) => c.id === 'conv-1'));
  if (!conversation) return null;
  return <ChatSectionElement conversation={conversation} isActive={false} onClick={vi.fn()} />;
}

async function choose(name: string) {
  const row = screen.getByRole('link', { name: 'Quarterly plan' }).parentElement as HTMLElement;
  fireEvent.mouseEnter(row);
  const trigger = within(row).getByRole('button', { name: 'Actions for Quarterly plan' });
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('menuitem', { name }));
  });
}

const toasts = () => useToastStore.getState().toasts.filter((t) => !t.isExiting);

beforeEach(() => {
  installBrowserShims();
  nav.conversationId = null;
  window.history.replaceState(null, '', '/chat/');
  router.push.mockReset();
  router.replace.mockReset();
  useToastStore.setState({ toasts: [] });
  Object.values(api).forEach((fn) => {
    fn.mockReset();
    fn.mockResolvedValue(undefined);
  });
  useChatStore.setState({ ...initialState, conversations: [chat()], conversationsVersion: 0 });
});
afterEach(() => cleanup());

describe('archiving your own chat from the sidebar', () => {
  it('archives at once, with no confirm, and the row leaves', async () => {
    renderInTheme(<StoreRow />);

    await choose('Archive');

    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(api.archiveConversation).toHaveBeenCalledWith('conv-1'));
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Quarterly plan' })).toBeNull());
  });

  it('says where the chat went in one toast, with Undo, on the success clock', async () => {
    renderInTheme(<StoreRow />);

    await choose('Archive');

    await waitFor(() => expect(toasts()).toHaveLength(1));
    const [archived] = toasts();
    expect(archived.title).toBe('Chat archived');
    expect(archived.description).toBe('Quarterly plan is in Workspace settings, under Archived chats.');
    expect(archived.action?.label).toBe('Undo');
    expect(archived.duration).toBe(TOAST_DEFAULT_DURATIONS.success);
  });

  it('Undo restores the chat once, through the existing restore, and puts its row back without a reload', async () => {
    renderInTheme(<StoreRow />);
    await choose('Archive');
    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(useChatStore.getState().conversations).toEqual([]);
    const versionBefore = useChatStore.getState().conversationsVersion;

    act(() => toasts()[0].action!.onClick!());

    await waitFor(() => expect(api.restoreConversation).toHaveBeenCalledTimes(1));
    expect(api.restoreConversation).toHaveBeenCalledWith('conv-1');
    await waitFor(() => expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['conv-1']));
    expect(useChatStore.getState().conversationsVersion).toBe(versionBefore);
    expect(router.push).not.toHaveBeenCalled();
  });

  it('archiving the open chat goes home, and Undo opens it again', async () => {
    nav.conversationId = 'conv-1';
    // The open chat, as the page's URL says.
    window.history.replaceState(null, '', '/chat/?conversationId=conv-1');
    renderInTheme(<StoreRow />);
    await choose('Archive');

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith('/chat/'));
    act(() => toasts()[0].action!.onClick!());
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(expect.stringContaining('conversationId=conv-1')));
  });

  it('a failed archive keeps the row and shows no archived toast', async () => {
    api.archiveConversation.mockRejectedValue(new Error('500'));
    renderInTheme(<StoreRow />);

    await choose('Archive');

    await waitFor(() => expect(api.archiveConversation).toHaveBeenCalled());
    expect(screen.getByRole('link', { name: 'Quarterly plan' })).toBeTruthy();
    expect(toasts().find((t) => t.title === 'Chat archived')).toBeUndefined();
  });
});

describe('renaming your own chat in the sidebar', () => {
  async function rename(to: string) {
    await choose('Rename');
    const field = await screen.findByRole('textbox', { name: 'New name' });
    fireEvent.change(field, { target: { value: to } });
    await act(async () => {
      fireEvent.keyDown(field, { key: 'Enter' });
      field.blur();
    });
  }

  it('edits in a Radix text field, in place', async () => {
    renderInTheme(<StoreRow />);
    await choose('Rename');
    const field = await screen.findByRole('textbox', { name: 'New name' });
    expect(field.closest('.rt-TextFieldRoot')?.classList.contains('rt-r-size-1')).toBe(true);
  });

  it('shows no toast when the rename works: the new title in the row is the answer', async () => {
    renderInTheme(<StoreRow />);

    await rename('Q3 plan');

    await waitFor(() => expect(api.renameConversation).toHaveBeenCalledWith('conv-1', 'Q3 plan'));
    expect(await screen.findByRole('link', { name: 'Q3 plan' })).toBeTruthy();
    expect(toasts()).toHaveLength(0);
  });

  it('says so in one error toast when the rename fails, and keeps the old title', async () => {
    api.renameConversation.mockRejectedValue(new Error('500'));
    renderInTheme(<StoreRow />);

    await rename('Q3 plan');

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0].variant).toBe('error');
    expect(toasts()[0].title).toBe("Couldn't rename the chat");
    expect(toasts()[0].description).toBe('It keeps its old name. Try again.');
    expect(screen.getByRole('link', { name: 'Quarterly plan' })).toBeTruthy();
  });
});
