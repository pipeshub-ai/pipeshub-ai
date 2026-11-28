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
  // The run-error words the stream reads (a code this build or an edition words); the defaults name none of these cases'.
  const rules = await vi.importActual<typeof import('@/app/(main)/chat/approval-ask-rules')>('@/app/(main)/chat/approval-ask-rules');
  const runErrors = await vi.importActual<typeof import('@/app/(main)/chat/run-error-extras')>('@/app/(main)/chat/run-error-extras');
  return {
    useAuthStore: auth.useAuthStore,
    approvalAskRules: rules.approvalAskRules,
    runErrorExtras: runErrors.runErrorExtras,
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
const { apiClient } = await import('@/lib/api');
const { ErrorType } = await import('@/lib/api/api-error');
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

  it.each([
    ['a new chat', null],
    ['a chat whose server has nothing of it', 'conv-1'],
  ])('a request that never reached the server keeps the line the app wrote: %s', async (_name, convId) => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    vi.spyOn(apiClient, 'get').mockRejectedValue({ type: ErrorType.NOT_FOUND, message: 'Not found', statusCode: 404 });
    expect(getThreadMessagePlainText(await failedRow(chat(convId)))).toBe(CHAT_STREAM_ERROR_MESSAGES.offline);
  });

  it("a run's own error is written for the reader and stays", async () => {
    const said = 'This request is not allowed right now. Ask your admin.';
    fetchMock.mockImplementationOnce(async () => sseResponse([sseFrame('RUN_ERROR', { type: 'RUN_ERROR', message: said, code: 'request_not_allowed' })]));
    expect(getThreadMessagePlainText(await failedRow(chat('conv-1')))).toBe(said);
  });

  it("a failed answer to a card keeps the server's text out of its toast too", async () => {
    const slotId = chat('conv-1');
    fetchMock.mockResolvedValueOnce(refusal(409, 'CONVERSATION_CHANGED'));
    await streamMessageForSlot(slotId, QUESTION, request, { resumeAskUserQuestion: true });
    expect(JSON.stringify(useToastStore.getState().toasts)).not.toContain(RAW);
  });
});

// In a chat with no feed, a failure seen live kept the rows only this tab
// held until a reload, so Try again asked the question anew as a new message, though the server had stored the
// question and its failure: after a reload the question showed twice.
describe('a failure the server stored', () => {
  const SAID = 'The model is unavailable. Try again in a moment.';
  const row = (_id: string, messageType: string, content: string) => ({ _id, messageType, content, createdAt: '2026-11-26T02:00:00.000Z' });
  const detail = (messages: unknown[]) => ({ data: { conversation: { id: 'conv-1', title: 't', messages, access: { isOwner: true, accessLevel: 'owner' } } } });
  const failedRun = () => sseResponse([sseFrame('RUN_ERROR', { type: 'RUN_ERROR', message: SAID, code: 'stream_error' })]);
  const ids = (slotId: string) => useChatStore.getState().slots[slotId].messages.map((m) => m.id);

  it('once its stream has ended, the turn takes the ids it was stored under, and shows what it showed', async () => {
    fetchMock.mockImplementationOnce(async () => failedRun());
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue(
      detail([row('m-old', 'user_query', 'earlier?'), row('m-old-a', 'bot_response', 'Earlier.'), row('m-q', 'user_query', QUESTION), row('m-err', 'error', RAW)]) as never,
    );
    const slotId = chat('conv-1');
    const failed = await failedRow(slotId);
    await vi.waitFor(() => expect(ids(slotId)).toEqual(['m-q', 'm-err']));
    const { messages, rawMessages } = useChatStore.getState().slots[slotId];
    expect(getThreadMessagePlainText(messages[1])).toBe(SAID);
    expect(messages[1].metadata).toEqual(failed.metadata);
    expect(rawMessages?.map((m) => m._id)).toEqual(['m-q', 'm-err']);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it('a question stored with no failure after it (not stored yet, or asked since) leaves the turn as this tab holds it', async () => {
    for (const stored of [
      [row('m-err-old', 'error', 'An older failure.'), row('m-q', 'user_query', QUESTION)],
      [row('m-q', 'user_query', QUESTION), row('m-a', 'bot_response', 'An answer.'), row('m-q2', 'user_query', 'next?'), row('m-err2', 'error', 'x')],
    ]) {
      fetchMock.mockImplementationOnce(async () => failedRun());
      const get = vi.spyOn(apiClient, 'get').mockResolvedValue(detail(stored) as never);
      const slotId = chat('conv-1');
      await failedRow(slotId);
      await vi.waitFor(() => expect(get).toHaveBeenCalled());
      await new Promise((r) => setTimeout(r, 20));
      expect(ids(slotId)).toEqual([undefined, expect.not.stringMatching(/^m-/)]);
      get.mockRestore();
    }
  });

  it('a send refused before its run began reads nothing back: the server stored nothing', async () => {
    fetchMock.mockResolvedValueOnce(refusal(429, 'RATE_LIMITED', 'Too many requests.'));
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue(detail([]) as never);
    await failedRow(chat('conv-1'));
    await new Promise((r) => setTimeout(r, 20));
    expect(get).not.toHaveBeenCalled();
  });
});
