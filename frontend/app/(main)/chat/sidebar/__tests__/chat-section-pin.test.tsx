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
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const api = vi.hoisted(() => ({ setConversationPinned: vi.fn() }));
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

/** Reads the row from the store, as the sidebar does, so the menu follows the pin. */
function StoreRow(props: { agentId?: string; projectId?: string; onPinnedChange?: (pinned: boolean) => void }) {
  const conversation = useChatStore((s) => s.conversations.find((c) => c.id === 'conv-1'));
  if (!conversation) return null;
  return <ChatSectionElement conversation={conversation} isActive={false} onClick={vi.fn()} {...props} />;
}

const pinnedInStore = () => useChatStore.getState().conversations.find((c) => c.id === 'conv-1')?.isPinned;
const version = () => useChatStore.getState().conversationsVersion;
const toastLines = () => useToastStore.getState().toasts.map((t) => `${t.variant}:${t.title}`);

async function openMenu() {
  const row = screen.getByRole('link', { name: 'Quarterly plan' }).parentElement as HTMLElement;
  fireEvent.mouseEnter(row);
  const trigger = within(row).getByRole('button');
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
}

async function choose(name: 'Pin' | 'Unpin') {
  await openMenu();
  await act(async () => {
    fireEvent.click(screen.getByRole('menuitem', { name }));
  });
}

beforeEach(() => {
  installBrowserShims();
  useToastStore.setState({ toasts: [] });
  api.setConversationPinned.mockReset();
  api.setConversationPinned.mockResolvedValue(undefined);
  useChatStore.setState({ ...initialState, conversations: [chat()], conversationsVersion: 0 });
});
afterEach(() => cleanup());

describe('pinning a chat from the sidebar', () => {
  it('pins the chat, and the menu then offers Unpin', async () => {
    renderInTheme(<StoreRow />);

    await choose('Pin');

    expect(api.setConversationPinned).toHaveBeenCalledWith('conv-1', true);
    expect(pinnedInStore()).toBe(true);
    await openMenu();
    expect(screen.getByRole('menuitem', { name: 'Unpin' })).toBeTruthy();
  });

  it('reloads the list once the server has confirmed, so the order matches the server’s', async () => {
    renderInTheme(<StoreRow />);

    await choose('Pin');

    await waitFor(() => expect(version()).toBe(1));
  });

  it('shows the pin before the server has answered', async () => {
    let answer: () => void = () => {};
    api.setConversationPinned.mockReturnValue(new Promise<void>((resolve) => (answer = resolve)));
    renderInTheme(<StoreRow />);

    await choose('Pin');

    expect(pinnedInStore()).toBe(true);
    expect(version()).toBe(0);
    await act(async () => answer());
    expect(version()).toBe(1);
  });

  it('unpins a pinned chat and removes the field', async () => {
    useChatStore.setState({ conversations: [chat({ isPinned: true })] });
    renderInTheme(<StoreRow />);

    await choose('Unpin');

    expect(api.setConversationPinned).toHaveBeenCalledWith('conv-1', false);
    expect(useChatStore.getState().conversations[0] && 'isPinned' in useChatStore.getState().conversations[0]).toBe(false);
  });

  it('puts the chat back and says so when pinning fails', async () => {
    api.setConversationPinned.mockRejectedValue(new Error('500'));
    renderInTheme(<StoreRow />);

    await choose('Pin');

    await waitFor(() => expect(toastLines()).toEqual(['error:Could not pin the chat']));
    expect(pinnedInStore()).toBeUndefined();
    expect(version()).toBe(0);
  });

  it('keeps the chat pinned and says so when unpinning fails', async () => {
    useChatStore.setState({ conversations: [chat({ isPinned: true })] });
    api.setConversationPinned.mockRejectedValue(new Error('500'));
    renderInTheme(<StoreRow />);

    await choose('Unpin');

    await waitFor(() => expect(toastLines()).toEqual(['error:Could not unpin the chat']));
    expect(pinnedInStore()).toBe(true);
    expect(version()).toBe(0);
  });

  it('sends one request when the menu item is chosen again before the first answer', async () => {
    let answer: () => void = () => {};
    api.setConversationPinned.mockReturnValue(new Promise<void>((resolve) => (answer = resolve)));
    renderInTheme(<StoreRow />);

    await choose('Pin');
    await choose('Unpin');

    expect(api.setConversationPinned).toHaveBeenCalledTimes(1);
    expect(pinnedInStore()).toBe(true);
    await act(async () => answer());
  });

  it('accepts the next change once the first has finished', async () => {
    renderInTheme(<StoreRow />);

    await choose('Pin');
    await waitFor(() => expect(version()).toBe(1));
    await choose('Unpin');

    expect(api.setConversationPinned.mock.calls).toEqual([
      ['conv-1', true],
      ['conv-1', false],
    ]);
    expect(pinnedInStore()).toBeUndefined();
  });

  it('tells a list that keeps its own rows about the change, and about the undo when it fails', async () => {
    const onPinnedChange = vi.fn();
    api.setConversationPinned.mockRejectedValue(new Error('500'));
    renderInTheme(<StoreRow onPinnedChange={onPinnedChange} />);

    await choose('Pin');

    await waitFor(() => expect(onPinnedChange.mock.calls).toEqual([[true], [false]]));
  });

  it('does not offer Pin on an agent chat, which has no pin', async () => {
    renderInTheme(<StoreRow agentId="agent-7" />);

    await openMenu();

    expect(screen.queryByRole('menuitem', { name: 'Pin' })).toBeNull();
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toBeTruthy();
  });

  it('does not offer Pin on a row in a project’s own list', async () => {
    renderInTheme(<StoreRow projectId="proj-1" />);

    await openMenu();

    expect(screen.queryByRole('menuitem', { name: 'Pin' })).toBeNull();
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toBeTruthy();
  });

  it('offers no menu at all on a chat someone else shared', () => {
    useChatStore.setState({ conversations: [chat({ isOwner: false })] });
    renderInTheme(<StoreRow />);

    const row = screen.getByRole('link', { name: /Quarterly plan/ }).parentElement as HTMLElement;
    fireEvent.mouseEnter(row);

    expect(within(row).queryByRole('button')).toBeNull();
  });
});
