/**
 * The More chats rows live in the chat store, so the row actions that change the rail's lists change them too.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import type { Conversation } from '@/chat/types';

installMemoryStorage();

const { useChatStore } = await import('@/chat/store');

function chat(id: string, extra: Partial<Conversation> = {}): Conversation {
  return {
    id,
    title: `Chat ${id}`,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    isShared: false,
    sharedWith: [],
    lastActivityAt: 1000,
    isOwner: true,
    ...extra,
  };
}

const store = () => useChatStore.getState();
const moreIds = () => store().moreChats.rows.map((c) => c.id);

/** Open the panel and load a first page of `ids`. */
function openWith(ids: string[], hasNextPage = false) {
  store().toggleMoreChatsPanel('your');
  const key = store().startMoreChatsList();
  store().setMoreChatsPage(key, ids.map((id) => chat(id)), 1, hasNextPage);
  return key;
}

beforeEach(() => {
  store().reset();
});

describe('the More chats list in the chat store', () => {
  it('opening the panel starts a loading list with no rows', () => {
    store().toggleMoreChatsPanel('shared');
    expect(store().moreChats).toMatchObject({ rows: [], isLoading: true, page: 0 });
  });

  it('page 1 replaces the rows; a later page appends only ids it does not have', () => {
    const key = openWith(['a', 'b'], true);
    store().setMoreChatsLoadingMore(key, true);
    expect(store().moreChats.isLoadingMore).toBe(true);
    store().setMoreChatsPage(key, [chat('b'), chat('c')], 2, false);
    expect(moreIds()).toEqual(['a', 'b', 'c']);
    expect(store().moreChats).toMatchObject({ page: 2, hasNextPage: false, isLoadingMore: false });
  });

  it('drops a page meant for an earlier list (another search or a closed panel)', () => {
    const oldKey = openWith(['a']);
    store().startMoreChatsList();
    store().setMoreChatsPage(oldKey, [chat('x')], 1, false);
    expect(moreIds()).toEqual([]);
    expect(store().moreChats.isLoading).toBe(true);
  });

  it('a failed load keeps the rows and stops loading', () => {
    const key = openWith(['a'], true);
    store().setMoreChatsLoadingMore(key, true);
    store().endMoreChatsLoad(key);
    expect(moreIds()).toEqual(['a']);
    expect(store().moreChats.isLoadingMore).toBe(false);
  });

  it('removeConversation takes the row out of More as well as the rail lists', () => {
    useChatStore.setState({ conversations: [chat('a'), chat('b')] });
    openWith(['a', 'b', 'c']);
    store().removeConversation('b');
    expect(moreIds()).toEqual(['a', 'c']);
    expect(store().conversations.map((c) => c.id)).toEqual(['a']);
  });

  it('renameConversation renames the row in More', () => {
    openWith(['a', 'b']);
    store().renameConversation('b', 'Budget');
    expect(store().moreChats.rows.map((c) => c.title)).toEqual(['Chat a', 'Budget']);
  });

  it('setConversationPinned pins the row in More and moves it first; unpinning clears it', () => {
    useChatStore.setState({ conversations: [chat('a'), chat('c')] });
    openWith(['a', 'b', 'c']);
    store().setConversationPinned('c', true);
    expect(moreIds()).toEqual(['c', 'a', 'b']);
    expect(store().moreChats.rows[0].isPinned).toBe(true);
    expect(store().conversations.map((c) => c.id)).toEqual(['c', 'a']);
    store().setConversationPinned('c', false);
    expect(store().moreChats.rows.find((c) => c.id === 'c')?.isPinned).toBeUndefined();
  });

  it('unpinning puts the chat back in server order (newest activity first), as the rail has it after its reload', () => {
    useChatStore.setState({ conversations: [] });
    const key = store().startMoreChatsList();
    store().setMoreChatsPage(key, [chat('a', { lastActivityAt: 3000 }), chat('b', { lastActivityAt: 2000 }), chat('c', { lastActivityAt: 1000 })], 1, false);

    store().setConversationPinned('c', true);
    expect(moreIds()).toEqual(['c', 'a', 'b']);
    store().setConversationPinned('b', true);
    expect(moreIds()).toEqual(['b', 'c', 'a']);

    store().setConversationPinned('b', false);
    expect(moreIds()).toEqual(['c', 'a', 'b']);
    store().setConversationPinned('c', false);
    expect(moreIds()).toEqual(['a', 'b', 'c']);
  });

  it('pinning a chat that is only in More still updates More', () => {
    useChatStore.setState({ conversations: [chat('a')] });
    openWith(['a', 'z']);
    store().setConversationPinned('z', true);
    expect(moreIds()).toEqual(['z', 'a']);
  });

  it('leaves the list object alone when nothing in it changes', () => {
    openWith(['a']);
    const before = store().moreChats;
    store().renameConversation('nope', 'x');
    store().removeConversation('nope');
    expect(store().moreChats).toBe(before);
  });

  it('Undo re-inserts an archived row at its former index, exactly once', () => {
    openWith(['a', 'b', 'c']);
    const place = store().snapshotMoreChatsRow('b');
    expect(place).toMatchObject({ index: 1 });
    store().removeConversation('b');
    store().restoreMoreChatsRow(place!);
    store().restoreMoreChatsRow(place!);
    expect(moreIds()).toEqual(['a', 'b', 'c']);
  });

  it('Undo does not touch a list that started over or a closed panel', () => {
    openWith(['a', 'b']);
    const place = store().snapshotMoreChatsRow('a');
    store().removeConversation('a');
    store().closeMoreChatsPanel();
    store().restoreMoreChatsRow(place!);
    expect(moreIds()).toEqual([]);
  });

  it('has no snapshot for a row that is not in More', () => {
    openWith(['a']);
    expect(store().snapshotMoreChatsRow('zz')).toBeNull();
  });
});
