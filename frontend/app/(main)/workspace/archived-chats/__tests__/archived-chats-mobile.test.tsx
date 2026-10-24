import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const env = vi.hoisted(() => ({ mobile: true, conversationId: null as string | null, push: vi.fn(), replace: vi.fn() }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => env.mobile, useIsBelowDesktop: () => env.mobile }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: env.push, replace: env.replace }),
  useSearchParams: () => new URLSearchParams(env.conversationId ? `conversationId=${env.conversationId}` : ''),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../components', () => ({
  ArchivedChatsSidebar: ({ onDeleted }: { onDeleted: (id: string, key: string | null) => void }) => (
    <button data-testid="list" onClick={() => onDeleted('c1', null)} />
  ),
  ArchivedChatView: () => <div data-testid="conversation" />,
  ArchivedChatsEmptyState: () => <div data-testid="empty" />,
  ArchivedChatSearch: () => null,
}));
vi.mock('../store', () => {
  const state = {
    conversations: [], agentGroups: [], isLoadingList: false, isLoadingAgentGroups: false,
    messagesMap: {}, selected: null, isLoadingConversation: false, conversationError: null,
    assistantPagination: {}, agentGroupsPagination: {},
    loadArchivedConversations: () => {}, loadAgentArchivedConversations: () => {}, loadConversation: () => {},
    loadMoreForAgent: () => {}, loadMoreAssistantChats: () => {}, loadMoreAgentGroups: () => {},
    removeConversation: () => {}, clearSelection: () => {},
  };
  return { useArchivedChatsStore: (sel: (s: typeof state) => unknown) => sel(state) };
});

import ArchivedChatsPage from '../page';

beforeEach(() => {
  env.mobile = true;
  env.conversationId = null;
  env.push.mockClear();
  env.replace.mockClear();
});
afterEach(cleanup);

const renderPage = () => render(<Theme><ArchivedChatsPage /></Theme>);

describe('archived chats on a phone', () => {
  it('shows only the list until a chat is chosen', () => {
    renderPage();
    expect(screen.getByTestId('list')).toBeTruthy();
    expect(screen.queryByTestId('conversation')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
  });

  it('shows only the conversation with a Back button that returns to the list', () => {
    env.conversationId = 'c1';
    renderPage();
    expect(screen.queryByTestId('list')).toBeNull();
    expect(screen.getByTestId('conversation')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(env.replace).toHaveBeenCalledWith('/workspace/archived-chats');
  });

  it('goes back to the list after a delete instead of opening another chat', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('list'));
    expect(env.replace).toHaveBeenCalledWith('/workspace/archived-chats');
    expect(env.push).not.toHaveBeenCalled();
  });

  it('keeps the list beside the conversation on larger screens', () => {
    env.mobile = false;
    env.conversationId = 'c1';
    renderPage();
    expect(screen.getByTestId('list')).toBeTruthy();
    expect(screen.getByTestId('conversation')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
  });
});
