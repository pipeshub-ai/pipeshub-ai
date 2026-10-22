/**
 * The sidebar's chat lists. By default they sit under a "Recents" toggle that starts closed, and "Your Chats" is split
 * by day; an edition's layout can show both lists when the page opens, with "Your Chats" as one list.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import type { Conversation } from '@/chat/types';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../chat-section-element', () => ({
  ChatSectionElement: ({ conversation }: { conversation: Conversation }) => <div data-testid="row">{conversation.title}</div>,
  GeneratingTitleItem: () => <div data-testid="row">Generating</div>,
  StartChatButton: () => null,
  ChatItemSkeleton: () => null,
}));

import { chatSlots } from '@/config';
import { useChatStore } from '@/chat/store';
import { defaultChatSlots } from '../../chat-extension';
import { ChatSections } from '../chat-sections';

const DAY = 24 * 60 * 60 * 1000;
const chat = (id: string, title: string, lastActivityAt: number) =>
  ({ id, title, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z', isShared: false, sharedWith: [], lastActivityAt }) as unknown as Conversation;
const rows = () => screen.queryAllByTestId('row').map((row) => row.textContent);

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

describe('ChatSections', () => {
  it('by default the lists sit under a closed Recents toggle, and Your Chats is split by day', () => {
    expect(defaultChatSlots.useSidebarLayout()).toEqual({ collapsibleRecents: true, timeGroupedChats: true });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(screen.queryByText('Shared Chats')).toBeNull();
    expect(rows()).toEqual([]);

    fireEvent.click(screen.getByText('Recents'));
    expect(screen.getByText('Shared Chats')).toBeTruthy();
    expect(screen.getByText('Your Chats')).toBeTruthy();
    expect(screen.getByText('Today')).toBeTruthy();
    expect(rows()).toEqual(['Shared plan', 'Today chat', 'Old chat']);
  });

  it('a layout without the toggle shows both lists on load, Your Chats as one list with new chats first', () => {
    vi.spyOn(chatSlots, 'useSidebarLayout').mockReturnValue({ collapsibleRecents: false, timeGroupedChats: false });
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.setState({ pendingConversations: { [slotId]: { slotId, title: null, isGenerating: true, createdAt: Date.now() } } });
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(screen.queryByText('Recents')).toBeNull();
    expect(screen.getByText('Shared Chats')).toBeTruthy();
    expect(screen.getByText('Your Chats')).toBeTruthy();
    expect(screen.queryByText('Today')).toBeNull();
    expect(rows()).toEqual(['Shared plan', 'Generating', 'Today chat', 'Old chat']);
  });
});
