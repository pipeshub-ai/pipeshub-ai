/**
 * A turn the backend answered without some tools: the non-fatal `tools_unavailable` event reaches the answer row,
 * whether the stored reply names them or not. Only `fetch` is faked.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn, sseFrame, sseResponse } from '@/lib/api/__tests__/sse-response';
import type { ConversationMessage, StreamChatRequest } from '../types';

installMemoryStorage();

vi.mock('@/config', async (importOriginal) => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  const { chatExtension, chatSlots } = await importOriginal<typeof import('@/config')>();
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn(), chatExtension, chatSlots };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useChatStore } = await import('../store');
const { streamMessageForSlot } = await import('../streaming');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();
const Q = 'Do you like coffee?';
const GMAIL = {
  kind: 'toolset',
  instanceId: 'ts-gmail',
  displayName: 'Gmail',
  problem: 'not_authenticated',
  sharedCredential: false,
  userCanConnect: true,
};

const frame = (type: string, fields: Record<string, unknown> = {}) => sseFrame(type, { type, ...fields });

function stored(overrides: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm',
    messageType: 'user_query',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    ...overrides,
  } as ConversationMessage;
}

function finished(answerExtra: Partial<ConversationMessage> = {}) {
  return {
    _id: 'conv-1',
    title: 'Coffee',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    isShared: false,
    status: 'complete',
    modelInfo: { modelKey: 'm1', modelName: 'gpt-5' },
    messages: [
      stored({ _id: 'q1', content: Q }),
      stored({ _id: 'a1', messageType: 'bot_response', content: 'I do.', ...answerExtra }),
    ],
  };
}

const request = { query: Q, modelKey: 'm1', modelName: 'gpt-5', chatMode: 'quick', filters: { apps: [], kb: [] } } as unknown as StreamChatRequest;

function answerCustom(slotId: string) {
  const rows = useChatStore.getState().slots[slotId].messages;
  return [...rows].reverse().find((m) => m.role === 'assistant')?.metadata?.custom as Record<string, unknown>;
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('tools_unavailable on a streamed turn', () => {
  it('the run answers, and the answer row lists the tools it went on without', async () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.setState({ activeSlotId: slotId });
    fetchMock.mockImplementationOnce(async () =>
      sseResponse([
        frame('CUSTOM', { name: 'conversation_created', value: { conversationId: 'conv-1', title: 'Coffee' } }),
        frame('CUSTOM', { name: 'tools_unavailable', value: { agentId: 'a-1', serviceAccount: false, items: [GMAIL] } }),
        frame('RUN_STARTED'),
        frame('TEXT_MESSAGE_START'),
        frame('TEXT_MESSAGE_CONTENT', { delta: 'I do.' }),
        frame('TEXT_MESSAGE_END'),
        frame('RUN_FINISHED', { result: { conversation: finished() } }),
      ]),
    );

    await streamMessageForSlot(slotId, Q, request);

    const custom = answerCustom(slotId);
    expect(custom.failed).toBeUndefined();
    expect(custom.unavailableTools).toEqual([GMAIL]);
  });

  it('the stored list wins over the live one', async () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.setState({ activeSlotId: slotId });
    const storedItem = { ...GMAIL, displayName: 'Gmail (work)' };
    fetchMock.mockImplementationOnce(async () =>
      sseResponse([
        frame('CUSTOM', { name: 'conversation_created', value: { conversationId: 'conv-1', title: 'Coffee' } }),
        frame('CUSTOM', { name: 'tools_unavailable', value: { items: [GMAIL] } }),
        frame('RUN_FINISHED', { result: { conversation: finished({ unavailableTools: [storedItem] } as never) } }),
      ]),
    );

    await streamMessageForSlot(slotId, Q, request);

    expect(answerCustom(slotId).unavailableTools).toEqual([storedItem]);
  });
});
