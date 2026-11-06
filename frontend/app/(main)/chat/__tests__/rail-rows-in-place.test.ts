/**
 * The store's in-place row changes a rail makes instead of reloading: a row's places kept and put back (Undo, a
 * refused archive), a new row listed where the server would list it (a fork, a chat back from a project).
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useChatStore } from '@/chat/store';
import type { Conversation } from '@/chat/types';

const row = (id: string, lastActivityAt = 1000, extra: Partial<Conversation> = {}): Conversation => ({
  id,
  title: `Chat ${id}`,
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
  isShared: false,
  sharedWith: [],
  isOwner: true,
  lastActivityAt,
  ...extra,
});
const ids = (rows: Conversation[]) => rows.map((c) => c.id);

beforeEach(() => useChatStore.getState().reset());

describe('snapshotRailRows / restoreRailRows', () => {
  it('puts a removed row back at its place in each list it was in, once', () => {
    useChatStore.setState({
      conversations: [row('a'), row('b'), row('c')],
      agentConversations: [row('g'), row('b')],
      agentConversationsAgentId: 'agent-1',
    });
    const snapshot = useChatStore.getState().snapshotRailRows('b');
    useChatStore.getState().removeConversation('b');
    expect(ids(useChatStore.getState().conversations)).toEqual(['a', 'c']);

    useChatStore.getState().restoreRailRows(snapshot);
    useChatStore.getState().restoreRailRows(snapshot);

    expect(ids(useChatStore.getState().conversations)).toEqual(['a', 'b', 'c']);
    expect(ids(useChatStore.getState().agentConversations)).toEqual(['g', 'b']);
  });

  it("does not put a row into another agent's lists", () => {
    useChatStore.setState({ agentConversations: [row('g1')], agentConversationsAgentId: 'agent-1' });
    const snapshot = useChatStore.getState().snapshotRailRows('g1');
    useChatStore.getState().removeConversation('g1');
    useChatStore.setState({ agentConversations: [row('x')], agentConversationsAgentId: 'agent-2' });

    useChatStore.getState().restoreRailRows(snapshot);

    expect(ids(useChatStore.getState().agentConversations)).toEqual(['x']);
  });

  it('keepUnread: a row moved away (into a project) keeps its new-answer dot', () => {
    useChatStore.setState({ conversations: [row('a')] });
    useChatStore.getState().markConversationUnread('a');

    useChatStore.getState().removeConversation('a', { keepUnread: true });

    expect(useChatStore.getState().unreadConversationIds.has('a')).toBe(true);
  });
});

describe('insertConversationRow', () => {
  it('lists a row after the pinned ones, newest activity first, as the server does', () => {
    useChatStore.setState({
      conversations: [row('p', 100, { isPinned: true }), row('new', 3000), row('old', 1000)],
    });

    useChatStore.getState().insertConversationRow(row('fork', 2000), 'conversations');

    expect(ids(useChatStore.getState().conversations)).toEqual(['p', 'new', 'fork', 'old']);
  });

  it('replaces a row it already has rather than listing it twice', () => {
    useChatStore.setState({ agentConversations: [row('a', 2000), row('b', 1000)] });

    useChatStore.getState().insertConversationRow(row('b', 3000), 'agentConversations');

    expect(ids(useChatStore.getState().agentConversations)).toEqual(['b', 'a']);
  });
});

describe('setConversationPinned, with no reload after it', () => {
  /** The More panel open on `section`, listing `rows`. */
  const moreOpen = (section: 'your' | 'shared', rows: Conversation[]) => {
    const key = useChatStore.getState().startMoreChatsList();
    useChatStore.setState({ isMoreChatsPanelOpen: true, moreChatsSectionType: section });
    useChatStore.getState().setMoreChatsPage(key, rows, 1, false);
  };
  const pinnedIds = (rows: Conversation[]) => ids(rows.filter((c) => c.isPinned === true));

  it('a chat pinned from More that the rail does not list joins Your chats, pinned first', () => {
    useChatStore.setState({ conversations: [row('a', 5000), row('b', 4000)] });
    moreOpen('your', [row('a', 5000), row('b', 4000), row('c', 3000)]);

    useChatStore.getState().setConversationPinned('c', true);

    expect(ids(useChatStore.getState().conversations)).toEqual(['c', 'a', 'b']);
    expect(pinnedIds(useChatStore.getState().conversations)).toEqual(['c']);
    expect(useChatStore.getState().sharedConversations).toEqual([]);
  });

  it("someone else's chat pinned from More joins Shared chats", () => {
    useChatStore.setState({ sharedConversations: [row('s1', 5000, { isOwner: false })] });
    moreOpen('shared', [row('s1', 5000, { isOwner: false }), row('s2', 4000, { isOwner: false })]);

    useChatStore.getState().setConversationPinned('s2', true);

    expect(ids(useChatStore.getState().sharedConversations)).toEqual(['s2', 's1']);
    expect(useChatStore.getState().conversations).toEqual([]);
  });

  it('a refused pin takes the row back out of the rail', () => {
    useChatStore.setState({ conversations: [row('a', 5000)] });
    moreOpen('your', [row('a', 5000), row('c', 3000)]);

    const undo = useChatStore.getState().setConversationPinned('c', true);
    undo();

    expect(ids(useChatStore.getState().conversations)).toEqual(['a']);
  });

  it('unpinned, an older chat goes back under the newer ones, where the server lists it', () => {
    useChatStore.setState({ conversations: [row('a', 5000), row('b', 4000), row('c', 3000)] });

    useChatStore.getState().setConversationPinned('c', true);
    useChatStore.getState().setConversationPinned('c', false);

    expect(ids(useChatStore.getState().conversations)).toEqual(['a', 'b', 'c']);
  });
});
