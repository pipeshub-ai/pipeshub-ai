/**
 * The final frame of a send carries only that turn's rows (`frame=turn`), so the
 * slot merges them by id into what it already shows instead of replacing it.
 * Only `fetch` is faked.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  installMemoryStorage,
  jwtExpiringIn,
  sseFrame,
  sseResponse,
} from '@/lib/api/__tests__/sse-response';
import type { StreamChatRequest, ConversationMessage } from '../types';

installMemoryStorage();

vi.mock('@/config', async (importOriginal) => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  const { chatExtension, chatSlots } = await importOriginal<typeof import('@/config')>();
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn(), chatExtension, chatSlots };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useChatStore } = await import('../store');
const { mergeById, streamMessageForSlot } = await import('../streaming');
const { getThreadMessagePlainText, loadHistoricalMessages } = await import('../runtime');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();
const Q = 'What was Q3 revenue?';

const frame = (type: string, fields: Record<string, unknown> = {}): string => sseFrame(type, { type, ...fields });

function row(_id: string, messageType: string, content = '', extra: Partial<ConversationMessage> = {}): ConversationMessage {
  return {
    _id,
    messageType,
    content,
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: '2026-10-16T00:00:00.000Z',
    updatedAt: '2026-10-16T00:00:00.000Z',
    ...extra,
  } as ConversationMessage;
}

/** A final frame; `turn` marks it as carrying only this turn's rows. */
function finished(messages: ConversationMessage[], turn = true) {
  return frame('RUN_FINISHED', {
    result: {
      conversation: {
        _id: 'conv-1',
        title: 'Quarterly revenue',
        status: 'Complete',
        modelInfo: { modelKey: 'm1', modelName: 'gpt-5' },
        messages,
        ...(turn ? { frame: 'turn' } : {}),
      },
    },
  });
}

const request = (overrides: Partial<StreamChatRequest> = {}): StreamChatRequest =>
  ({
    query: Q,
    modelKey: 'm1',
    modelName: 'gpt-5',
    modelFriendlyName: 'GPT-5',
    chatMode: 'quick',
    filters: { apps: [], kb: [] },
    conversationId: 'conv-1',
    ...overrides,
  }) as StreamChatRequest;

/** A slot showing `rows` as a load of the conversation leaves it. */
function openWith(rows: ConversationMessage[], pagination = { currentPage: 1, hasOlderMessages: false, isLoadingOlder: false }): string {
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.setState({ activeSlotId: slotId });
  useChatStore.getState().updateSlot(slotId, {
    messages: loadHistoricalMessages(rows).messages,
    rawMessages: rows,
    hasLoaded: true,
    messagePagination: pagination,
  });
  return slotId;
}

const slot = (id: string) => useChatStore.getState().slots[id];
const texts = (id: string) => slot(id).messages.map((m) => [m.role, getThreadMessagePlainText(m)]);

function respondWith(chunks: string[]) {
  fetchMock.mockImplementationOnce(async (_url, init) => sseResponse({ chunks, signal: init?.signal }));
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('mergeById', () => {
  it('replaces a known row where it stands and adds the rest at the end, never matching a row without a key', () => {
    const rows = [{ id: 'a', v: 1 }, { id: undefined, v: 2 }, { id: 'b', v: 3 }];
    const incoming = [{ id: 'b', v: 30 }, { id: 'c', v: 40 }, { id: undefined, v: 50 }];

    expect(mergeById(rows, incoming, (r) => r.id)).toEqual([
      { id: 'a', v: 1 },
      { id: undefined, v: 2 },
      { id: 'b', v: 30 },
      { id: 'c', v: 40 },
      { id: undefined, v: 50 },
    ]);
  });
});

describe('a send whose final frame carries only its turn', () => {
  it('asks the server for that frame', async () => {
    const slotId = openWith([]);
    respondWith([finished([row('q1', 'user_query', Q), row('a1', 'bot_response', 'Revenue grew.')])]);

    await streamMessageForSlot(slotId, Q, request());

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/conversations/conv-1/messages/stream?frame=turn');
  });

  it('keeps the rows already shown and puts the stored question and answer where the live ones were', async () => {
    const earlier = [row('q0', 'user_query', 'Earlier question'), row('a0', 'bot_response', 'Earlier answer')];
    const slotId = openWith(earlier);
    respondWith([finished([row('q1', 'user_query', Q), row('a1', 'bot_response', 'Revenue grew.')])]);

    await streamMessageForSlot(slotId, Q, request());

    expect(slot(slotId).messages.map((m) => m.id)).toEqual(['q0', 'a0', 'q1', 'a1']);
    expect(texts(slotId)).toEqual([
      ['user', 'Earlier question'],
      ['assistant', 'Earlier answer'],
      ['user', Q],
      ['assistant', 'Revenue grew.'],
    ]);
    expect(slot(slotId).rawMessages?.map((m) => m._id)).toEqual(['q0', 'a0', 'q1', 'a1']);
    expect(slot(slotId).isStreaming).toBe(false);
  });

  it('keeps an earlier turn that only this tab shows, such as a failed one', async () => {
    const slotId = openWith([row('q0', 'user_query', 'Earlier question'), row('a0', 'bot_response', 'Earlier answer')]);
    respondWith([frame('RUN_ERROR', { message: 'The answer failed.', code: 'internal_error' })]);
    await streamMessageForSlot(slotId, 'Failed question', request({ query: 'Failed question' }));
    const afterFailure = texts(slotId);
    expect(afterFailure).toHaveLength(4);

    respondWith([finished([row('q2', 'user_query', Q), row('a2', 'bot_response', 'Revenue grew.')])]);
    await streamMessageForSlot(slotId, Q, request());

    expect(texts(slotId)).toEqual([...afterFailure, ['user', Q], ['assistant', 'Revenue grew.']]);
  });

  it('folds the answer to a question card into the card it answers', async () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-region',
        question: 'Which region?',
        multiSelect: false,
        options: [{ id: 'eu', label: 'EU', isUserInput: false }, { id: 'us', label: 'US', isUserInput: false }],
      }],
    };
    const slotId = openWith([]);
    respondWith([
      frame('CUSTOM', { name: 'ask_user_question', value: { toolData: payload } }),
      finished([
        row('q1', 'user_query', Q),
        row('t1', 'tool_call', '', { tools: [{ toolName: 'ask_user_question', toolResult: payload }] } as Partial<ConversationMessage>),
        row('a1', 'bot_response', ''),
      ]),
    ]);
    await streamMessageForSlot(slotId, Q, request());
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: {
        ...slot(slotId).pendingAskUserQuestion!,
        answers: { 'q-region': { questionUuid: 'q-region', selectedOptionIds: ['eu'], userInputs: {} } },
        status: 'submitted',
      },
    });

    const picked = 'User selections:\n1. "Which region?" → EU';
    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'EU it is.' }),
      finished([row('sel', 'user_query', picked), row('a2', 'bot_response', 'EU it is.')]),
    ]);
    await streamMessageForSlot(slotId, picked, request({ query: picked }), { resumeAskUserQuestion: true });

    expect(slot(slotId).pendingAskUserQuestion?.status).toBe('submitted');
    expect(texts(slotId)).toEqual([
      ['user', Q],
      ['assistant', 'EU it is.'],
    ]);
  });

  it('starts the next older page past the stored rows it now holds', async () => {
    const loaded = Array.from({ length: 38 }, (_, i) => row(`r${i}`, i % 2 === 0 ? 'user_query' : 'bot_response', `Row ${i}`));
    const slotId = openWith(loaded, { currentPage: 1, hasOlderMessages: true, isLoadingOlder: false });
    respondWith([finished([row('q1', 'user_query', Q), row('a1', 'bot_response', 'Revenue grew.')])]);

    await streamMessageForSlot(slotId, Q, request());

    // 40 stored rows held: pages 1 and 2 of 20; the next load asks for page 3.
    expect(slot(slotId).messagePagination).toEqual({ currentPage: 2, hasOlderMessages: true, isLoadingOlder: false });
  });
});

describe('a final frame from a server that sends every row', () => {
  it('replaces the list as before', async () => {
    const slotId = openWith([row('q0', 'user_query', 'Earlier question'), row('a0', 'bot_response', 'Earlier answer')]);
    respondWith([finished([row('q1', 'user_query', Q), row('a1', 'bot_response', 'Revenue grew.')], false)]);

    await streamMessageForSlot(slotId, Q, request());

    expect(slot(slotId).messages.map((m) => m.id)).toEqual(['q1', 'a1']);
    expect(slot(slotId).messagePagination?.currentPage).toBe(1);
  });
});
