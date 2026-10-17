import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

installMemoryStorage();

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

const { useChatStore } = await import('@/chat/store');
const { ChatSectionElement } = await import('../chat-section-element');

const initialState = useChatStore.getState();
const unread = () => [...useChatStore.getState().unreadConversationIds];

const conversation = { id: 'conv-1', title: 'Quarterly revenue', isOwner: false } as never;

function renderRow(isActive = false) {
  return renderInTheme(<ChatSectionElement conversation={conversation} isActive={isActive} onClick={vi.fn()} />);
}

const dot = () => screen.queryByRole('img', { name: 'New answer' });

beforeEach(() => {
  installBrowserShims();
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, unreadConversationIds: new Set() });
});

afterEach(() => cleanup());

describe('a chat row in the sidebar', () => {
  it('shows no dot for a chat with nothing new', () => {
    renderRow();

    expect(screen.getByText('Quarterly revenue')).toBeTruthy();
    expect(dot()).toBeNull();
  });

  it('shows a dot, announced as a new answer, when an answer finished out of view', () => {
    useChatStore.getState().markConversationUnread('conv-1');

    renderRow();

    expect(screen.getByText('Quarterly revenue')).toBeTruthy();
    expect(dot()).not.toBeNull();
  });

  it('shows the dot only on the chat that has a new answer', () => {
    useChatStore.getState().markConversationUnread('conv-other');

    renderRow();

    expect(dot()).toBeNull();
  });

  it('adds the dot when an answer finishes while the row is already on screen', () => {
    renderRow();
    expect(dot()).toBeNull();

    act(() => useChatStore.getState().markConversationUnread('conv-1'));

    expect(dot()).not.toBeNull();
  });

  it('removes the dot once the user opens the chat', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    const { rerender } = renderRow(false);
    expect(dot()).not.toBeNull();

    rerender(<ChatSectionElement conversation={conversation} isActive onClick={vi.fn()} />);

    expect(dot()).toBeNull();
    expect(unread()).toEqual([]);
  });

  it('never shows a dot on the chat the user is already in', () => {
    useChatStore.getState().markConversationUnread('conv-1');

    renderRow(true);

    expect(dot()).toBeNull();
    expect(unread()).toEqual([]);
  });

  it('keeps the other unread chats unread when one is opened', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    useChatStore.getState().markConversationUnread('conv-2');

    renderRow(true);

    expect(unread()).toEqual(['conv-2']);
  });

  it('shows the running indicator, not the dot, while that chat is answering again', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, { isStreaming: true });

    renderRow();

    expect(dot()).toBeNull();
  });
});
