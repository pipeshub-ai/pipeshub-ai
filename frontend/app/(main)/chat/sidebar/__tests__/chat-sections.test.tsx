/**
 * The sidebar's chat lists sit under a "Recents" toggle that starts closed, with "Your Chats" split by day; an
 * edition can leave rows out of both lists.
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
  it('the lists sit under a closed Recents toggle, and Your Chats is split by day', () => {
    render(<ChatSections onOpenMoreChats={() => {}} />);
    expect(screen.queryByText('Shared Chats')).toBeNull();
    expect(rows()).toEqual([]);

    fireEvent.click(screen.getByText('Recents'));
    expect(screen.getByText('Shared Chats')).toBeTruthy();
    expect(screen.getByText('Your Chats')).toBeTruthy();
    expect(screen.getByText('Today')).toBeTruthy();
    expect(rows()).toEqual(['Shared plan', 'Today chat', 'Old chat']);
  });

  it('leaves out the rows the edition hides, in both lists', () => {
    vi.spyOn(chatSlots, 'useVisibleConversations').mockImplementation((rows) => rows.filter((row) => row.id !== 's1' && row.id !== 'c2'));
    render(<ChatSections onOpenMoreChats={() => {}} />);
    fireEvent.click(screen.getByText('Recents'));
    expect(rows()).toEqual(['Today chat']);
  });
});
