/**
 * What a refused send writes on the failed row of a chat with one person. Only `fetch` is faked, so each case runs the
 * real path from the HTTP answer to the row. A row never shows the server's own text, unless it says what to fix or the
 * chat knows its code; text the app wrote stays, and so do the question and the failed mark Try again hangs on.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installMemoryStorage, jsonResponse, jwtExpiringIn, sseFrame, sseResponse } from '@/lib/api/__tests__/sse-response';
import type { ThreadMessageLike } from '@assistant-ui/react';
import type { StreamChatRequest } from '../types';

installMemoryStorage();

const config = vi.hoisted(() => ({ chatExtension: null as unknown, chatSlots: null as unknown }));
vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return {
    useAuthStore: auth.useAuthStore,
    logoutAndRedirect: vi.fn(),
    get chatExtension() {
      return config.chatExtension;
    },
    get chatSlots() {
      return config.chatSlots;
    },
  };
});

const { defaultChatExtension, defaultChatSlots } = await import('../chat-extension');
config.chatExtension = defaultChatExtension;
config.chatSlots = defaultChatSlots;
const { useAuthStore } = await import('@/lib/store/auth-store');
const { useChatStore } = await import('../store');
const { useToastStore } = await import('@/lib/store/toast-store');
const { getThreadMessagePlainText } = await import('../runtime');
const { streamMessageForSlot } = await import('../streaming');
const { CHAT_STREAM_ERROR_MESSAGES } = await import('@/lib/api/stream-errors');
const { default: i18n } = await import('i18next');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();
const RAW = 'RAW-SERVER-TEXT internal detail';
const QUESTION = 'What changed?';
const generic = () => i18n.t('chatStream.errorFallback');

const refusal = (status: number, code: string | undefined, message = RAW) =>
  jsonResponse(status, { error: { ...(code ? { code } : {}), message } });

function chat(convId: string | null): string {
  const slotId = useChatStore.getState().createSlot(convId);
  useChatStore.getState().updateSlot(slotId, { isInitialized: true, isOwner: true, messages: [] });
  useChatStore.setState({ activeSlotId: slotId });
  return slotId;
}

const request = { query: QUESTION, chatMode: 'agent' } as unknown as StreamChatRequest;

async function failedRow(slotId: string): Promise<ThreadMessageLike> {
  await streamMessageForSlot(slotId, QUESTION, request);
  const { messages } = useChatStore.getState().slots[slotId];
  expect(getThreadMessagePlainText(messages[0])).toBe(QUESTION);
  const row = messages[messages.length - 1];
  expect((row.metadata?.custom as { failed?: boolean }).failed).toBe(true);
  return row;
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  config.chatExtension = defaultChatExtension;
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  useToastStore.getState().clearAll();
  window.sessionStorage.clear();
  window.history.replaceState(null, '', '/chat/');
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the failed row of a refused send', () => {
  it.each([
    ['an existing chat', 'conv-1'],
    ['a new chat', null],
  ])('in %s, an unknown code reads as the generic line, with the question and the failed row kept', async (_name, convId) => {
    fetchMock.mockResolvedValueOnce(refusal(409, 'CONVERSATION_CHANGED'));
    const row = await failedRow(chat(convId));
    expect(getThreadMessagePlainText(row)).toBe(generic());
  });

  it('a 4xx with no code at all is the server talking too', async () => {
    fetchMock.mockResolvedValueOnce(refusal(400, undefined));
    expect(getThreadMessagePlainText(await failedRow(chat('conv-1')))).toBe(generic());
  });

  it('a failed validation keeps its reason, which says what to fix', async () => {
    fetchMock.mockResolvedValueOnce(refusal(400, 'VALIDATION_ERROR', 'Your message is too long.'));
    expect(getThreadMessagePlainText(await failedRow(chat('conv-1')))).toBe('Your message is too long.');
  });

  it('a code the chat knows keeps its text', async () => {
    config.chatExtension = {
      ...defaultChatExtension,
      stream: { ...defaultChatExtension.stream, knowsErrorCode: (code: string | undefined) => code === 'A_KNOWN_CODE' },
    };
    fetchMock.mockResolvedValueOnce(refusal(409, 'A_KNOWN_CODE', 'This chat is read-only.'));
    expect(getThreadMessagePlainText(await failedRow(chat('conv-1')))).toBe('This chat is read-only.');
  });

  it.each([
    ['a 401', 401, CHAT_STREAM_ERROR_MESSAGES.sessionExpired],
    ['a 500', 500, CHAT_STREAM_ERROR_MESSAGES.unavailable],
    ['a 429', 429, 'PipesHub is busy right now. Please try again in a few seconds.'],
  ])('%s keeps the line the app wrote, whatever its code', async (_name, status, text) => {
    fetchMock.mockResolvedValueOnce(refusal(status, 'SOME_CODE'));
    expect(getThreadMessagePlainText(await failedRow(chat('conv-1')))).toBe(text);
  });

  it('a 403 with no readable reason keeps the line the app wrote', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(403, {}));
    expect(getThreadMessagePlainText(await failedRow(chat('conv-1')))).toBe(CHAT_STREAM_ERROR_MESSAGES.forbidden);
  });

  it('a request that never reached the server keeps the line the app wrote', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    expect(getThreadMessagePlainText(await failedRow(chat('conv-1')))).toBe(CHAT_STREAM_ERROR_MESSAGES.offline);
  });

  it("a run's own error is written for the reader and stays", async () => {
    fetchMock.mockImplementationOnce(async () => sseResponse([sseFrame('RUN_ERROR', { type: 'RUN_ERROR', message: 'This request was refused.', code: 'ai_budget_exceeded' })]));
    expect(getThreadMessagePlainText(await failedRow(chat('conv-1')))).toBe('This request was refused.');
  });

  it("a failed answer to a card keeps the server's text out of its toast too", async () => {
    const slotId = chat('conv-1');
    fetchMock.mockResolvedValueOnce(refusal(409, 'CONVERSATION_CHANGED'));
    await streamMessageForSlot(slotId, QUESTION, request, { resumeAskUserQuestion: true });
    expect(JSON.stringify(useToastStore.getState().toasts)).not.toContain(RAW);
  });
});
