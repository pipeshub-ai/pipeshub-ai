import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';

const backing = installMemoryStorage();

const { useChatStore } = await import('../../store');
const { useAuthStore } = await import('@/config');
const { allowChatUnreadWrites, chatUnreadKey } = await import('@/lib/storage/chat-unread');
const { useHydrateUnreadConversations } = await import('../use-hydrate-unread-conversations');

function tokenFor(userId: string): string {
  const payload = btoa(JSON.stringify({ userId, exp: Math.floor(Date.now() / 1000) + 3600 }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `header.${payload}.signature`;
}

function saveFor(userId: string, ...conversationIds: string[]) {
  const entries = Object.fromEntries(conversationIds.map((id) => [id, Date.now()]));
  backing.set(chatUnreadKey(userId) as string, JSON.stringify(entries));
}

const unread = () => [...useChatStore.getState().unreadConversationIds].sort();

beforeEach(() => {
  backing.clear();
  allowChatUnreadWrites();
  useChatStore.getState().reset();
  useAuthStore.setState({ accessToken: null, user: null });
});

describe('useHydrateUnreadConversations', () => {
  it('restores the signed-in user’s saved chats when the page opens', () => {
    saveFor('user-1', 'conv-1', 'conv-2');
    useAuthStore.setState({ accessToken: tokenFor('user-1') });

    renderHook(() => useHydrateUnreadConversations());

    expect(unread()).toEqual(['conv-1', 'conv-2']);
  });

  it('restores nothing while nobody is signed in', () => {
    saveFor('user-1', 'conv-1');

    renderHook(() => useHydrateUnreadConversations());

    expect(unread()).toEqual([]);
  });

  it('restores the chats once the sign-in finishes after the page opened', () => {
    saveFor('user-1', 'conv-1');
    renderHook(() => useHydrateUnreadConversations());
    expect(unread()).toEqual([]);

    act(() => useAuthStore.setState({ accessToken: tokenFor('user-1') }));

    expect(unread()).toEqual(['conv-1']);
  });

  it('swaps to the new user’s chats when a different person signs in', () => {
    saveFor('user-1', 'conv-of-user-1');
    saveFor('user-2', 'conv-of-user-2');
    useAuthStore.setState({ accessToken: tokenFor('user-1') });
    renderHook(() => useHydrateUnreadConversations());
    expect(unread()).toEqual(['conv-of-user-1']);

    act(() => useAuthStore.setState({ accessToken: tokenFor('user-2') }));

    expect(unread()).toEqual(['conv-of-user-2']);
  });

  it('keeps the chats when the same user’s token is refreshed', () => {
    saveFor('user-1', 'conv-1');
    useAuthStore.setState({ accessToken: tokenFor('user-1') });
    renderHook(() => useHydrateUnreadConversations());

    act(() => useAuthStore.setState({ accessToken: `${tokenFor('user-1')}x` }));

    expect(unread()).toEqual(['conv-1']);
  });
});
