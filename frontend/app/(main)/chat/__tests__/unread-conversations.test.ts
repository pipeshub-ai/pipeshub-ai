/**
 * The set of chats whose answer finished while the user was elsewhere: how a
 * chat gets in, and every way it gets out.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';

const backing = installMemoryStorage();

const { useChatStore } = await import('../store');
const { useAuthStore } = await import('@/lib/store/auth-store');
const { allowChatUnreadWrites, chatUnreadKey, loadUnreadConversationIds } = await import('@/lib/storage/chat-unread');

const initialState = useChatStore.getState();
const unread = () => [...useChatStore.getState().unreadConversationIds];

/** A login token whose payload names the user, as the real one does. */
function tokenFor(userId: string): string {
  const payload = btoa(JSON.stringify({ userId, exp: Math.floor(Date.now() / 1000) + 3600 }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `header.${payload}.signature`;
}

function signInAs(userId: string | null) {
  useAuthStore.setState({ accessToken: userId ? tokenFor(userId) : null, user: null });
}

/** What is saved for this user, as a sorted list. */
const saved = (userId: string) => [...loadUnreadConversationIds(userId)].sort();

beforeEach(() => {
  backing.clear();
  allowChatUnreadWrites();
  useChatStore.getState().reset();
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, unreadConversationIds: new Set() });
  signInAs('user-1');
});

describe('marking chats unread and read', () => {
  it('starts empty', () => {
    expect(unread()).toEqual([]);
  });

  it('remembers a chat as unread', () => {
    useChatStore.getState().markConversationUnread('conv-1');

    expect(unread()).toEqual(['conv-1']);
  });

  it('keeps several chats unread at once', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    useChatStore.getState().markConversationUnread('conv-2');

    expect(unread()).toEqual(['conv-1', 'conv-2']);
  });

  it('does not rewrite the store when a chat is already unread', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    const before = useChatStore.getState().unreadConversationIds;

    useChatStore.getState().markConversationUnread('conv-1');

    expect(useChatStore.getState().unreadConversationIds).toBe(before);
  });

  it('clears one chat and leaves the others', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    useChatStore.getState().markConversationUnread('conv-2');

    useChatStore.getState().markConversationRead('conv-1');

    expect(unread()).toEqual(['conv-2']);
  });

  it('does not rewrite the store when a chat was not unread', () => {
    const before = useChatStore.getState().unreadConversationIds;

    useChatStore.getState().markConversationRead('conv-1');

    expect(useChatStore.getState().unreadConversationIds).toBe(before);
  });

  it('never changes a set a component may still be holding', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    const held = useChatStore.getState().unreadConversationIds;

    useChatStore.getState().markConversationUnread('conv-2');
    useChatStore.getState().markConversationRead('conv-1');

    expect([...held]).toEqual(['conv-1']);
  });
});

describe('opening a chat reads it', () => {
  it('clears the chat when its slot becomes the open one', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().markConversationUnread('conv-1');

    useChatStore.getState().setActiveSlot(slotId);

    expect(unread()).toEqual([]);
  });

  it('leaves other unread chats alone', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().markConversationUnread('conv-1');
    useChatStore.getState().markConversationUnread('conv-2');

    useChatStore.getState().setActiveSlot(slotId);

    expect(unread()).toEqual(['conv-2']);
  });

  it('is unaffected by opening a chat that was never unread', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().markConversationUnread('conv-2');
    const before = useChatStore.getState().unreadConversationIds;

    useChatStore.getState().setActiveSlot(slotId);

    expect(useChatStore.getState().unreadConversationIds).toBe(before);
  });

  it('copes with opening a brand-new chat that has no conversation yet', () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().markConversationUnread('conv-2');

    useChatStore.getState().setActiveSlot(slotId);

    expect(unread()).toEqual(['conv-2']);
  });
});

describe('a chat that goes away', () => {
  it('is no longer unread once it is deleted', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    useChatStore.getState().markConversationUnread('conv-2');

    useChatStore.getState().removeConversation('conv-1');

    expect(unread()).toEqual(['conv-2']);
  });

  it('leaves the unread chats alone when a chat that was not unread is deleted', () => {
    useChatStore.getState().markConversationUnread('conv-2');

    useChatStore.getState().removeConversation('conv-1');

    expect(unread()).toEqual(['conv-2']);
  });

  it('forgets every unread chat on a full reset', () => {
    useChatStore.getState().markConversationUnread('conv-1');

    useChatStore.getState().reset();

    expect(unread()).toEqual([]);
  });
});

describe('surviving a reload', () => {
  /** What a reload does: memory is lost, then the chat page restores this user's chats. */
  function reload(userId: string | null = 'user-1') {
    useChatStore.setState({ unreadConversationIds: new Set() });
    if (userId) useChatStore.getState().hydrateUnreadConversations(userId);
  }

  it('saves a chat that becomes unread', () => {
    useChatStore.getState().markConversationUnread('conv-1');

    expect(saved('user-1')).toEqual(['conv-1']);
  });

  it('brings the chat back as unread after a reload', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    useChatStore.getState().markConversationUnread('conv-2');

    reload();

    expect(unread().sort()).toEqual(['conv-1', 'conv-2']);
  });

  it('stops saving a chat once it is read', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    useChatStore.getState().markConversationUnread('conv-2');

    useChatStore.getState().markConversationRead('conv-1');

    expect(saved('user-1')).toEqual(['conv-2']);
    reload();
    expect(unread()).toEqual(['conv-2']);
  });

  it('stops saving a chat once it is opened, whichever way', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().markConversationUnread('conv-1');

    useChatStore.getState().setActiveSlot(slotId);

    expect(saved('user-1')).toEqual([]);
    reload();
    expect(unread()).toEqual([]);
  });

  it('stops saving a chat that is deleted', () => {
    useChatStore.getState().markConversationUnread('conv-1');

    useChatStore.getState().removeConversation('conv-1');

    expect(saved('user-1')).toEqual([]);
  });

  it('leaves other saved chats alone when one is opened or deleted', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().markConversationUnread('conv-1');
    useChatStore.getState().markConversationUnread('conv-2');
    useChatStore.getState().markConversationUnread('conv-3');

    useChatStore.getState().setActiveSlot(slotId);
    useChatStore.getState().removeConversation('conv-2');

    expect(saved('user-1')).toEqual(['conv-3']);
  });

  it('keeps a chat unread in memory but saves nothing when nobody is signed in', () => {
    signInAs(null);

    useChatStore.getState().markConversationUnread('conv-1');

    expect(unread()).toEqual(['conv-1']);
    expect(backing.size).toBe(0);
  });

  it('uses the user object when the token does not name the user', () => {
    useAuthStore.setState({ accessToken: 'header.e30.signature', user: { id: 'user-from-profile' } });

    useChatStore.getState().markConversationUnread('conv-1');

    expect(saved('user-from-profile')).toEqual(['conv-1']);
  });

  it('keeps one user’s chats out of another user’s sidebar', () => {
    useChatStore.getState().markConversationUnread('conv-1');

    signInAs('user-2');
    reload('user-2');

    expect(unread()).toEqual([]);
    expect(saved('user-1')).toEqual(['conv-1']);
  });

  it('keeps chats that became unread before the page finished restoring', () => {
    backing.set(chatUnreadKey('user-1') as string, JSON.stringify({ 'conv-saved': Date.now() }));
    useChatStore.getState().markConversationUnread('conv-early');

    useChatStore.getState().hydrateUnreadConversations('user-1');

    expect(unread().sort()).toEqual(['conv-early', 'conv-saved']);
  });

  it('drops the previous user’s chats from memory when someone else signs in', () => {
    useChatStore.getState().hydrateUnreadConversations('user-1');
    useChatStore.getState().markConversationUnread('conv-of-user-1');
    backing.set(chatUnreadKey('user-2') as string, JSON.stringify({ 'conv-of-user-2': Date.now() }));

    useChatStore.getState().hydrateUnreadConversations('user-2');

    expect(unread()).toEqual(['conv-of-user-2']);
  });

  it('does not rewrite the store when there is nothing new to restore', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    const before = useChatStore.getState().unreadConversationIds;

    useChatStore.getState().hydrateUnreadConversations('user-1');

    expect(useChatStore.getState().unreadConversationIds).toBe(before);
  });

  it('restores nothing, without failing, when nothing was saved', () => {
    useChatStore.getState().hydrateUnreadConversations('user-1');

    expect(unread()).toEqual([]);
  });

  it('forgets whose chats were restored on a full reset', () => {
    useChatStore.getState().hydrateUnreadConversations('user-1');
    useChatStore.getState().markConversationUnread('conv-1');
    useChatStore.getState().reset();
    backing.set(chatUnreadKey('user-2') as string, JSON.stringify({ 'conv-2': Date.now() }));
    useChatStore.getState().markConversationUnread('conv-after-reset');

    useChatStore.getState().hydrateUnreadConversations('user-2');

    expect(unread().sort()).toEqual(['conv-2', 'conv-after-reset']);
  });
});
