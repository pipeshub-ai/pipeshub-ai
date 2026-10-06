/** A feed page and the question card: another person's card opens read-only, an answered one closes. */
import { describe, it, expect, beforeEach } from 'vitest';
import type { ConversationMessage } from '../../types';

const { useChatStore } = await import('../../store');
const { applyFeedPage } = await import('../apply-feed-page');

const initialState = useChatStore.getState();
const ASK = { name: 'ask_user_question', questions: [{ uuid: 'q1', question: 'Which region?', options: [{ id: 'eu', label: 'EU' }] }] };
const bob = { userId: 'b', displayName: 'Bob' };

function stored(over: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm', messageType: 'user_query', content: '', contentFormat: 'MARKDOWN', citations: [],
    followUpQuestions: [], feedback: [], createdAt: '2026-10-01T00:00:00.000Z', updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  } as ConversationMessage;
}

const page = (messages: ConversationMessage[], rev: number) =>
  ({ messages, rev, nextSeq: 9, hasMore: false, activeRun: null, lastActivityAt: 1 }) as never;

const askerTurn = [
  stored({ _id: 'q', content: 'Plan it', seq: 0, author: bob }),
  stored({ _id: 'bot', messageType: 'bot_response', content: '', seq: 2, requestedBy: bob, author: bob }),
  stored({ _id: 'tc', messageType: 'tool_call', seq: 1, requestedBy: bob, tools: [{ toolName: 'ask_user_question', toolResult: ASK }] } as Partial<ConversationMessage>),
];

function openSlot() {
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(slotId, {
    isInitialized: true,
    rev: 1,
    access: { role: 'owner', isOwner: true, accessLevel: 'owner', canSend: true, canManage: true, canInvite: true, isCollaborative: true },
  });
  useChatStore.setState({ activeSlotId: slotId });
  return slotId;
}
const slot = (id: string) => useChatStore.getState().slots[id];

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
});

describe('applyFeedPage, question cards', () => {
  it('opens the card another person was asked, bound to that person and to its stored row', () => {
    const id = openSlot();
    expect(applyFeedPage(id, page(askerTurn, 2))).toBe(true);
    expect(slot(id).pendingAskUserQuestion).toMatchObject({ toolCallMessageId: 'tc', requestedBy: bob, status: 'pending' });
  });

  it('closes the card once the page shows it answered', () => {
    const id = openSlot();
    applyFeedPage(id, page(askerTurn, 2));
    applyFeedPage(id, page([
      stored({ _id: 'q2', content: 'User selections: EU', seq: 3, author: bob }),
      stored({ _id: 'bot2', messageType: 'bot_response', content: 'Done', seq: 4, requestedBy: bob, author: bob }),
    ], 3));
    expect(slot(id).pendingAskUserQuestion).toBeNull();
  });

  it('keeps a card this tab opened itself and is still collecting answers for', () => {
    const id = openSlot();
    const own = { assistantMessageId: 'bot', payload: ASK as never, answers: { q1: { questionUuid: 'q1', selectedOptionIds: ['eu'], userInputs: {} } }, status: 'pending' as const };
    useChatStore.getState().updateSlot(id, { pendingAskUserQuestion: own });
    applyFeedPage(id, page(askerTurn, 2));
    expect(slot(id).pendingAskUserQuestion).toBe(own);
  });
});
