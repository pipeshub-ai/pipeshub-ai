import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, renderHook, cleanup } from '@testing-library/react';
import type { ThreadMessageLike } from '@assistant-ui/react';
import '@/lib/__tests__/test-i18n';

// `../store` pulls in `@/lib/api`, whose auth store reads localStorage at import time; nothing here makes a request.
vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const { defaultChatExtension: ext, defaultChatSlots: slots } = await import('../chat-extension');
const { useChatStore } = await import('../store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
import type { ChatSlot, ConversationMessage, StreamChatRequest } from '../types';
import type { MessagePair } from '../components/message-area/message-pairs';

afterEach(cleanup);

const msg = { _id: 'm1', messageType: 'user_query', content: 'hi' } as ConversationMessage;
const slot = { convId: 'c1', isOwner: true, messages: [] } as unknown as ChatSlot;
const request = { query: 'hi', conversationId: 'c1' } as StreamChatRequest;
const thread: ThreadMessageLike = { role: 'user', content: [{ type: 'text', text: 'hi' }] };
const pair = { key: 'p', question: 'q', answer: 'a', citationMaps: {} as MessagePair['citationMaps'], isStreaming: false } as MessagePair;

describe('default chat extension (today’s chat)', () => {
  it('leaves titles, rows, requests and pairs as they are', () => {
    expect(ext.formatConversationTitle('Hello')).toBe('Hello');
    expect(ext.formatConversationTitle(undefined)).toBeUndefined();
    expect(ext.conversationFields({ access: { isOwner: false } })).toEqual({});
    expect(ext.agentStreamBodyFields(request)).toEqual({});
    expect(ext.slot.defaults(true)).toEqual({});
    expect(ext.slot.ownerFields()).toEqual({});
    expect(ext.slot.isRetired(slot)).toBe(false);
    expect(ext.send.hold({ slotId: 's', slot, query: 'hi', message: thread })).toBe(false);
    expect(ext.send.requestFields(slot, thread, { isAgent: false })).toEqual({});
    expect(ext.send.newMessageFields(request, thread)).toEqual({});
    expect(ext.question.truncate('abcdef', 3)).toBe('abc');
    expect(ext.question.toPlainText('a <b>')).toBe('a <b>');
    expect(ext.pairs.isStandalone({ role: 'system', content: [] })).toBe(false);
    expect(ext.pairs.decorate(pair, { standaloneBefore: 0 })).toBe(pair);
  });

  it('keeps no other slot on screen, and calls each conversation at its usual path', () => {
    expect(ext.slot.isOnScreen(slot, 'another-slot')).toBe(false);
    expect(ext.slot.isOnScreen(slot, null)).toBe(false);
    expect(ext.conversationPath('c1')).toBe('/api/v1/conversations/c1');
    expect(ext.conversationPath('c1', null)).toBe('/api/v1/conversations/c1');
    expect(ext.conversationPath('c1', 'agent-1')).toBe('/api/v1/agents/agent-1/conversations/c1');
  });

  it('lets anyone answer any question card, and any card answer close the open card', () => {
    expect(ext.history.cardIsAnswerable({})).toBe(true);
    expect(ext.history.answersCard(msg, {})).toBe(true);
  });

  it('loads history and streams as before', () => {
    const messages = [msg];
    expect(ext.history.prepare(messages)).toBe(messages);
    expect(ext.history.loadOptions()).toEqual({});
    expect(ext.history.detailFields({ access: { isOwner: true } })).toEqual({});
    expect(ext.history.loadFailureFields(new Error('x'))).toBeNull();
    expect(ext.history.rowCustom(msg, {})).toEqual({});
    expect(ext.history.turnCustomFromToolRow(msg)).toBeNull();
    expect(ext.history.mapRow(msg, {})).toBeUndefined();
    expect(ext.history.askCardFields(msg, { messages, index: 0, cardRowId: 'm1' })).toEqual({});
    expect(ext.stream.begin('s', slot, request)).toBeNull();
    expect(ext.stream.startFields()).toEqual({});
    expect(ext.stream.skipsAnswer({ conversationId: 'c1' } as never)).toBe(false);
    expect(ext.stream.resumeFailedMessage(new Error('x'))).toBeNull();
    expect(() => ext.stream.onCustomEvent('s', 'anything', {})).not.toThrow();
  });
});

describe('default chat slots', () => {
  it('owner-only access: Share for the owner, the composer unless the user is known not to own the chat', () => {
    const owned = useChatStore.getState().createSlot('c-own');
    useChatStore.getState().updateSlot(owned, { isOwner: true });
    const shared = useChatStore.getState().createSlot('c-shared');
    useChatStore.getState().updateSlot(shared, { isOwner: false });
    const loading = useChatStore.getState().createSlot('c-loading');

    expect(renderHook(() => slots.useChatAccess(owned)).result.current).toEqual({
      isOwner: true,
      canShare: true,
      showComposer: true,
      shareAgentChats: true,
    });
    expect(renderHook(() => slots.useChatAccess(shared)).result.current).toMatchObject({ canShare: false, showComposer: false });
    expect(renderHook(() => slots.useChatAccess(loading)).result.current).toMatchObject({ canShare: false, showComposer: true });
  });

  it('someone a durable chat was shared with for editing gets the composer while the harness is on for the org', () => {
    const harness = (harnessOn: boolean) =>
      useFeatureFlagsStore.setState({
        agentHarness: { useHarness: harnessOn, harnessMajor: 2, reason: 'org', resumeInFlightRuns: true, harnessOn },
      } as never);
    const chat = (id: string, fields: object) => {
      const slotId = useChatStore.getState().createSlot(id);
      useChatStore.getState().updateSlot(slotId, { isOwner: false, ...fields });
      return slotId;
    };
    const editing = chat('c-edit', { accessLevel: 'write', durable: true });
    const classic = chat('c-edit-classic', { accessLevel: 'write', durable: false });
    const reading = chat('c-read-durable', { accessLevel: 'read', durable: true });
    const agentChat = chat('c-edit-agent', { accessLevel: 'write', durable: true, threadAgentId: 'agent-1' });
    const composer = (slotId: string) => renderHook(() => slots.useChatAccess(slotId)).result.current;
    try {
      harness(true);
      expect(composer(editing)).toEqual({ isOwner: false, canShare: false, showComposer: true, shareAgentChats: true });
      // Today's loop serves only the owner, a read share stays read-only, and an agent's chat takes its owner's turns only.
      for (const slotId of [classic, reading, agentChat]) expect(composer(slotId).showComposer, slotId).toBe(false);
      harness(false);
      expect(composer(editing).showComposer).toBe(false);
    } finally {
      useFeatureFlagsStore.setState({ agentHarness: null } as never);
    }
  });

  it('while the harness is paused for the org, someone a durable chat was shared with for editing has no composer', () => {
    // On for the org, but taking no new turn now (the kill switch, an operator stop): their send would go to today's
    // loop, which refuses it with 403 COLLABORATOR_TURNS_DISABLED.
    useFeatureFlagsStore.setState({
      agentHarness: { useHarness: false, harnessMajor: 2, reason: 'org', resumeInFlightRuns: true, harnessOn: true },
    } as never);
    const editing = useChatStore.getState().createSlot('c-edit-paused');
    useChatStore.getState().updateSlot(editing, { isOwner: false, accessLevel: 'write', durable: true });
    const owned = useChatStore.getState().createSlot('c-own-paused');
    useChatStore.getState().updateSlot(owned, { isOwner: true, durable: true });
    try {
      expect(renderHook(() => slots.useChatAccess(editing)).result.current.showComposer).toBe(false);
      // The owner's turn still goes to today's loop, which serves them.
      expect(renderHook(() => slots.useChatAccess(owned)).result.current.showComposer).toBe(true);
    } finally {
      useFeatureFlagsStore.setState({ agentHarness: null } as never);
    }
  });

  it("an answer in a chat someone else owns is theirs: no 'you stopped', and no offer to connect their tools", () => {
    const shared = useChatStore.getState().createSlot('c-read');
    useChatStore.getState().updateSlot(shared, { isOwner: false });
    const owned = useChatStore.getState().createSlot('c-mine');
    useChatStore.getState().updateSlot(owned, { isOwner: true });
    const input = { question: 'q', isStreaming: false, isLastMessage: true, unanswered: false }

    useChatStore.setState({ activeSlotId: shared });
    const read = renderHook(() => slots.useResponseExtension(input)).result.current;
    expect(read.stoppedByOther).toEqual({
      title: 'The person who asked stopped this response',
      partialBody: 'What was written before it stopped is kept above.',
    });
    expect(read.toolsConnectReadOnlyFor).toEqual({ agentName: null });
    // Who answers its approval cards is named only when the shared list says who owns the chat.
    expect(read.approvalCardReadOnlyFor).toBeUndefined();
    useChatStore.setState({ sharedConversations: [{ id: 'c-read', sharedBy: { userId: 'o1', name: 'Owner Person' } } as never] });
    expect(renderHook(() => slots.useResponseExtension(input)).result.current.approvalCardReadOnlyFor).toEqual({ name: 'Owner Person' });
    useChatStore.setState({ sharedConversations: [] });

    useChatStore.setState({ activeSlotId: owned });
    const mine = renderHook(() => slots.useResponseExtension(input)).result.current;
    expect(mine).toEqual({ hideTabs: false, hideConfidence: false, allowRegenerate: true });
    useChatStore.setState({ activeSlotId: null });
  });

  it('adds nothing around the composer, the list or a row', async () => {
    const send = renderHook(() => slots.useComposerSend({ isAgentChat: false, agentId: null })).result.current;
    expect(send.intercept('hi', undefined, undefined)).toBe(false);
    expect(send.messageCustom(undefined)).toEqual({});
    expect(send.accessories).toBeNull();
    // Absent: a streaming answer swaps Send for Stop.
    expect(send.sendDuringRun).toBeUndefined();
    const input = renderHook(() =>
      slots.useComposerInput({ composerRef: { current: null }, message: 'hi', setMessage: () => {}, slotId: null }),
    ).result.current;
    expect(input.rich).toBe(false);
    expect(input.resolveSend('hi', () => {})).toEqual({ text: 'hi' });
    expect(renderHook(() => slots.useEmptyState({ slotId: null, onPick: () => {} })).result.current).toBeNull();
    const rows = [{ id: 'a' }];
    expect(renderHook(() => slots.useVisibleConversations(rows)).result.current).toBe(rows);
    expect(renderHook(() => slots.useAgentSharedConversations([])).result.current).toEqual([]);
    const response = renderHook(() =>
      slots.useResponseExtension({ question: 'q', isStreaming: false, isLastMessage: true, unanswered: false }),
    ).result.current;
    expect(response).toEqual({ hideTabs: false, hideConfidence: false, allowRegenerate: true });
    // Absent: the question offers Edit.
    expect(response.allowEditQuery).toBeUndefined();
    const row = renderHook(() =>
      slots.useChatRow({
        conversation: { id: 'c' } as never,
        ownerMenu: 'menu',
        isHovered: false,
        onMenuOpenChange: () => {},
        applyProjectChange: async () => {},
        isMutationBlocked: () => false,
      }),
    ).result.current;
    expect(row.rightSlot).toBe('menu');
    expect(row.interceptProjectChange('p1')).toBe(false);

    const header = renderHook(() =>
      slots.useChatHeader({
        slotId: null,
        conversationId: null,
        threadAgentId: null,
        pageAgentId: null,
        showConversationShare: false,
        openShare: () => {},
      }),
    ).result.current;
    expect(header.render(null)).toBeNull();
    const { getByText } = render(<>{header.render(<span>Share</span>)}</>);
    expect(getByText('Share')).toBeTruthy();

    const body = render(
      <slots.MessageListBody
        pairs={[]}
        isStreaming={false}
        renderDefault={() => <p>default list</p>}
        renderResponse={() => null}
        setMessageRef={() => {}}
      />,
    );
    expect(body.getByText('default list')).toBeTruthy();
    expect(render(<slots.QueryText text="plain <b>" />).getByText('plain <b>')).toBeTruthy();
    expect(render(<slots.ComposerBanners slotId={null} hasMessages={false} />).container.innerHTML).toBe('');
  });

  it('draws the default message list, and nothing beside the chat', () => {
    const list = render(<slots.MessageList renderDefault={() => <p>the message list</p>} />);
    expect(list.getByText('the message list')).toBeTruthy();
    expect(render(<slots.SidePanel slotId="s1" />).container.innerHTML).toBe('');
    expect(render(<slots.SidePanel slotId={null} />).container.innerHTML).toBe('');
  });
});
