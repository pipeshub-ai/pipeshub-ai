/**
 * A stream whose connection drops while the server goes on with the run: the turn stays as it is, running, the server
 * is asked for the run, and its stored answer settles the turn. Only a server that has neither the run nor its answer
 * makes it a failure. `fetch` (the streams) and the axios adapter (the reads) are faked, so these cover what a person
 * sees in the thread from the drop to the end.
 */
import { describe, it, expect, beforeEach, afterEach, onTestFinished, vi } from 'vitest';
import { AxiosError } from 'axios';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { installMemoryStorage, jwtExpiringIn, sseFrame, sseResponse, type StreamPlan } from '@/lib/api/__tests__/sse-response';
import type { ConversationMessage, PendingAskUserQuestion, StreamChatRequest } from '../types';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  const { defaultChatExtension } = await vi.importActual<typeof import('@/app/(main)/chat/chat-extension')>(
    '@/app/(main)/chat/chat-extension',
  );
  return { ...auth, logoutAndRedirect: vi.fn(), chatExtension: defaultChatExtension };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useChatStore } = await import('../store');
const { streamMessageForSlot, streamRegenerateForSlot, cancelStreamForSlot } = await import('../streaming');
const { GIVE_UP_AFTER_MS, regenerationOnServer, sendOnServer, withReadRows } = await import('../utils/follow-dropped-run');
const { getThreadMessagePlainText, loadHistoricalMessages } = await import('../runtime');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { CHAT_STREAM_ERROR_MESSAGES } = await import('@/lib/api/stream-errors');
const { useToastStore } = await import('@/lib/store/toast-store');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();

const CONV = 'conv-1';
const Q = 'What was Q3 revenue?';
const HALF = 'Revenue was';
const WHOLE = 'Revenue was 4.2 million.';
const MODEL = { modelKey: 'm1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' };

const frame = (type: string, fields: Record<string, unknown> = {}) => sseFrame(type, { type, ...fields });
const HALF_STREAMED = [frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: HALF })];

function stored(overrides: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm',
    messageType: 'user_query',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: '2026-09-18T00:00:00.000Z',
    updatedAt: '2026-09-18T00:00:00.000Z',
    ...overrides,
  } as ConversationMessage;
}

const EARLIER = [
  stored({ _id: 'u1', content: 'Hello' }),
  stored({ _id: 'a1', messageType: 'bot_response', content: 'Hi there.' }),
];

/** The conversation as the server holds it, read through its detail route; `down`: it can't be reached. */
function server(rows: ConversationMessage[], agentId?: string) {
  const detail = agentId ? `GET /api/v1/agents/${agentId}/conversations/${CONV}` : `GET /api/v1/conversations/${CONV}/`;
  const state = { rows: [...rows], status: 'Complete', down: false, hangs: false, gone: false, cancels: [] as unknown[] };
  const api = fakeApi({
    [detail]: (config) => {
      if (state.hangs) return new Promise<never>(() => {});
      if (state.down) throw new AxiosError('Network Error', 'ERR_NETWORK', config);
      if (state.gone) return { status: 404, data: { message: 'Not found' } };
      return {
        status: 200,
        data: {
          conversation: {
            id: CONV, title: 'Revenue', isShared: false, sharedWith: [], status: state.status, modelInfo: MODEL,
            messages: state.rows, access: { isOwner: true, accessLevel: 'read' },
            pagination: { page: 1, limit: 20, totalCount: state.rows.length, totalPages: 1, hasNextPage: false, hasPrevPage: false },
          },
        },
      };
    },
    [`POST /api/v1/conversations/${CONV}/cancel`]: (config) => {
      state.cancels.push(JSON.parse(String(config.data)));
      return { status: 200, data: { cancelled: true } };
    },
  });
  return Object.assign(state, {
    checks: () => api.count(detail),
    /** The run took the question (or a card's answer) and goes on. */
    asked(content = Q, id = 'u2') {
      state.rows.push(stored({ _id: id, content }));
      state.status = 'Inprogress';
    },
    answered(content = WHOLE, over: Partial<ConversationMessage> = {}) {
      state.rows.push(stored({ _id: 'a2', messageType: 'bot_response', content, ...over }));
      state.status = 'Complete';
    },
  });
}

function request(overrides: Partial<StreamChatRequest> = {}): StreamChatRequest {
  return { query: Q, ...MODEL, chatMode: 'quick', filters: { apps: [], kb: [] }, conversationId: CONV, ...overrides } as StreamChatRequest;
}

function openChat(rows: ConversationMessage[] = EARLIER): string {
  const slotId = useChatStore.getState().createSlot(CONV);
  useChatStore.getState().updateSlot(slotId, {
    messages: loadHistoricalMessages(rows).messages,
    rawMessages: rows,
    isInitialized: true,
    hasLoaded: true,
  });
  useChatStore.setState({ activeSlotId: slotId });
  return slotId;
}

const slot = (id: string) => useChatStore.getState().slots[id];
const texts = (id: string) => slot(id).messages.map((m) => [m.role, getThreadMessagePlainText(m)]);
const failedRows = (id: string) =>
  slot(id).messages.filter((m) => (m.metadata?.custom as { failed?: boolean } | undefined)?.failed).length;
const statusOf = (id: string) => slot(id).currentStatusMessage?.status;
const toasts = () => useToastStore.getState().toasts.map((t) => t.title);

function respond(plan: StreamPlan | string[]) {
  const p = Array.isArray(plan) ? { chunks: plan } : plan;
  fetchMock.mockImplementationOnce(async (_url, init) => sseResponse({ ...p, signal: init?.signal }));
}
const drops = (chunks: string[] = HALF_STREAMED) => respond({ chunks, failWith: new TypeError('network error') });
const pass = (ms: number) => vi.advanceTimersByTimeAsync(ms);

beforeEach(() => {
  vi.useFakeTimers();
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(7200), refreshToken: 'r' });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  useToastStore.getState().clearAll();
  window.history.replaceState(null, '', '/chat/');
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('a question whose stream drops', () => {
  it('stays being written, with what streamed, while the server runs it; then its whole answer takes its place', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    const run = streamMessageForSlot(slotId, Q, request());
    srv.asked();
    await pass(9_000);

    expect(slot(slotId)).toMatchObject({ isStreaming: true, streamingContent: HALF, remoteRunInProgress: false });
    expect(statusOf(slotId)).toBe('calling_llm');
    expect(failedRows(slotId)).toBe(0);
    expect(slot(slotId).messages).toHaveLength(4);

    srv.answered();
    await pass(3_000);
    await run;

    expect(slot(slotId)).toMatchObject({ isStreaming: false, streamingContent: '', runId: null, abortController: null });
    expect(texts(slotId)).toEqual([['user', 'Hello'], ['assistant', 'Hi there.'], ['user', Q], ['assistant', WHOLE]]);
    expect(slot(slotId).rawMessages?.map((m) => m._id)).toEqual(['u1', 'a1', 'u2', 'a2']);
    // One run, and nothing said about the connection.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(toasts()).toEqual([]);
  });

  it('whose run had ended by then shows the whole answer at once', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    srv.answered();
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(500);
    await run;

    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', WHOLE]]);
    expect(slot(slotId).isStreaming).toBe(false);
  });

  it('is a failure with Try again only once the server has had neither run nor answer for a while', async () => {
    server(EARLIER);
    const slotId = openChat();
    drops([]);
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(GIVE_UP_AFTER_MS - 100);
    expect(slot(slotId).isStreaming).toBe(true);
    expect(failedRows(slotId)).toBe(0);
    expect(statusOf(slotId)).toBe('reconnecting');

    await pass(200);
    await run;
    expect(slot(slotId).isStreaming).toBe(false);
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', CHAT_STREAM_ERROR_MESSAGES.interrupted]]);
    expect(failedRows(slotId)).toBe(1);
    expect(useChatStore.getState().pendingConversations[slotId]).toBeUndefined();
  });

  it('whose request never got an answer says it could not reach the server, once the server has nothing of it', async () => {
    server(EARLIER);
    const slotId = openChat();
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(GIVE_UP_AFTER_MS + 100);
    await run;
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', CHAT_STREAM_ERROR_MESSAGES.offline]]);
  });

  it('whose request got no answer but reached the server is followed like any other', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(0);
    srv.asked();
    await pass(4_000);
    expect(slot(slotId).isStreaming).toBe(true);
    srv.answered();
    await pass(3_000);
    await run;
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', WHOLE]]);
  });

  it('two drops: a server that can not be reached has said nothing, however long it takes', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    srv.down = true;
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(5 * 60_000);
    expect(slot(slotId)).toMatchObject({ isStreaming: true, streamingContent: HALF });
    expect(statusOf(slotId)).toBe('reconnecting');
    expect(failedRows(slotId)).toBe(0);

    srv.down = false;
    await pass(3_000);
    expect(slot(slotId).isStreaming).toBe(true);
    expect(statusOf(slotId)).toBe('calling_llm');
    // The connection goes again, and comes back to a finished run.
    srv.down = true;
    await pass(10_000);
    expect(statusOf(slotId)).toBe('reconnecting');
    srv.answered();
    srv.down = false;
    await pass(3_000);
    await run;
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', WHOLE]]);
    expect(toasts()).toEqual([]);
  });

  it.each(['online', 'focus'])('is asked for at once when the window says %s', async (event) => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    srv.hangs = true;
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(2_000);
    srv.hangs = false;
    srv.answered();
    window.dispatchEvent(new Event(event));
    await pass(0);
    await run;
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', WHOLE]]);
  });

  it('a check that hangs on the connection that went does not hold the next one back', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    srv.hangs = true;
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(9_000);
    srv.hangs = false;
    srv.answered();
    await pass(2_500);
    await run;
    expect(slot(slotId).isStreaming).toBe(false);
  });

  it.each([
    ['closes without its last frame', HALF_STREAMED],
    ['ends with the server cutting it short', [...HALF_STREAMED, frame('RUN_ERROR', { message: 'aborted', code: 'abort' })]],
  ])('is the same when it %s', async (_name, chunks) => {
    const srv = server(EARLIER);
    const slotId = openChat();
    respond(chunks);
    srv.asked();
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(4_000);
    expect(slot(slotId)).toMatchObject({ isStreaming: true, streamingContent: HALF });
    srv.answered('Revenue was', { status: 'stopped' });
    await pass(3_000);
    await run;
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', 'Revenue was']]);
    expect(slot(slotId).messages[3].metadata?.custom?.status).toBe('stopped');
  });

  it('whose answer is among the rows the tab already has needs no read of the server', async () => {
    const srv = server(EARLIER);
    srv.down = true;
    const slotId = openChat();
    const gone = new TypeError('network error');
    let fail!: () => void;
    fetchMock.mockImplementationOnce(async () =>
      new Response(new ReadableStream<Uint8Array>({
        start(controller) {
          for (const chunk of HALF_STREAMED) controller.enqueue(new TextEncoder().encode(chunk));
          fail = () => controller.error(gone);
        },
      }), { status: 200 }));
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(100);
    useChatStore.getState().updateSlot(slotId, {
      rawMessages: [...EARLIER, stored({ _id: 'u2', content: Q }), stored({ _id: 'a2', messageType: 'bot_response', content: WHOLE })],
    });
    fail();
    await pass(0);
    await run;
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', WHOLE]]);
    expect(srv.checks()).toBe(0);
  });

  it('is settled in the same tick by rows that reach the tab another way, the streamed answer never beside the stored one', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(4_000);
    const seen: string[][] = [];
    onTestFinished(useChatStore.subscribe(() => seen.push(texts(slotId).map(([, text]) => text))));
    useChatStore.getState().updateSlot(slotId, {
      rawMessages: [...EARLIER, stored({ _id: 'u2', content: Q }), stored({ _id: 'a2', messageType: 'bot_response', content: WHOLE })],
    });
    expect(slot(slotId).isStreaming).toBe(false);
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', WHOLE]]);
    await run;
    expect(seen.every((rows) => rows.filter((text) => text === WHOLE).length <= 1)).toBe(true);
  });

  it('seen running has the same time again before it is nothing: its answer may be on its way to the rows', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(60_000);
    // The run has let the chat go, and its row is not there yet.
    srv.status = 'Complete';
    await pass(GIVE_UP_AFTER_MS - 4_000);
    expect(slot(slotId).isStreaming).toBe(true);
    srv.answered();
    await pass(3_000);
    await run;
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', WHOLE]]);
  });

  it('whose run is gone without a word fails, once it has been gone for a while', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(60_000);
    srv.status = 'Complete';
    await pass(GIVE_UP_AFTER_MS + 4_000);
    await run;
    expect(failedRows(slotId)).toBe(1);
  });

  it('whose run failed on the server shows the stored failure, with nothing of its own', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    srv.answered('The AI model is not responding right now.', { messageType: 'error' });
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(100);
    await run;
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', 'The AI model is not responding right now.']]);
    expect(failedRows(slotId)).toBe(1);
  });

  it('in a chat that is gone, or no longer theirs, is a failure at once', async () => {
    const srv = server(EARLIER);
    srv.gone = true;
    const slotId = openChat();
    drops();
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(0);
    await run;
    expect(failedRows(slotId)).toBe(1);
    expect(toasts()).toEqual([]);
  });

  it('in a new chat whose id never came is a failure at once: there is nothing to ask about', async () => {
    server([]);
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.setState({ activeSlotId: slotId });
    drops();
    const run = streamMessageForSlot(slotId, Q, request({ conversationId: undefined }));
    await pass(0);
    await run;
    expect(texts(slotId)).toEqual([['user', Q], ['assistant', CHAT_STREAM_ERROR_MESSAGES.interrupted]]);
    expect(useChatStore.getState().pendingConversations[slotId]).toBeUndefined();
  });

  it('in a new chat whose id came is followed, and the chat is listed when it ends', async () => {
    const srv = server([]);
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.setState({ activeSlotId: slotId });
    drops([frame('CUSTOM', { name: 'conversation_created', value: { conversationId: CONV, title: 'Revenue' } }), ...HALF_STREAMED]);
    srv.asked();
    const run = streamMessageForSlot(slotId, Q, request({ conversationId: undefined }));
    await pass(4_000);
    expect(slot(slotId)).toMatchObject({ isStreaming: true, streamingContent: HALF, convId: CONV });
    srv.answered();
    await pass(3_000);
    await run;
    expect(texts(slotId)).toEqual([['user', Q], ['assistant', WHOLE]]);
    expect(slot(slotId).isTemp).toBe(false);
    expect(useChatStore.getState().conversations.map((c) => [c.id, c.title])).toEqual([[CONV, 'Revenue']]);
  });

  it('asked again in the same words is not settled with the earlier answer', async () => {
    const rows = [stored({ _id: 'u1', content: Q }), stored({ _id: 'a1', messageType: 'bot_response', content: 'The old answer.' })];
    server(rows);
    const slotId = openChat(rows);
    drops([]);
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(10_000);
    expect(slot(slotId).isStreaming).toBe(true);
    await pass(GIVE_UP_AFTER_MS);
    await run;
    expect(texts(slotId)).toEqual([
      ['user', Q], ['assistant', 'The old answer.'], ['user', Q], ['assistant', CHAT_STREAM_ERROR_MESSAGES.interrupted],
    ]);
  });

  it("is read by its agent's route in an agent's chat", async () => {
    const srv = server(EARLIER, 'agent-1');
    const slotId = openChat();
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-1' });
    drops();
    srv.asked();
    const run = streamMessageForSlot(slotId, Q, request({ agentId: 'agent-1' }));
    await pass(4_000);
    expect(slot(slotId).isStreaming).toBe(true);
    srv.answered();
    await pass(3_000);
    await run;
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', WHOLE]]);
  });

  it('a later question whose stream drops too is followed the same way', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    srv.answered();
    const first = streamMessageForSlot(slotId, Q, request());
    await pass(100);
    await first;
    drops();
    const second = streamMessageForSlot(slotId, 'And Q4?', request({ query: 'And Q4?' }));
    srv.asked('And Q4?', 'u3');
    await pass(4_000);
    expect(slot(slotId)).toMatchObject({ isStreaming: true, streamingContent: HALF });
    srv.answered('Higher.', { _id: 'a3' });
    await pass(3_000);
    await second;
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', WHOLE], ['user', 'And Q4?'], ['assistant', 'Higher.']]);
  });
});

describe('Stop while the run is asked for', () => {
  it('stops it on the server, and the stopped answer it stored is shown', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(4_000);
    const { runId } = slot(slotId);
    cancelStreamForSlot(slotId);
    expect(slot(slotId).stopping).toBe(true);
    await pass(0);
    expect(srv.cancels).toEqual([{ runId }]);
    srv.answered('Revenue was up', { status: 'stopped' });
    await pass(1_000);
    await run;
    expect(slot(slotId)).toMatchObject({ isStreaming: false, stopping: false, remoteRunInProgress: false });
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', 'Revenue was up']]);
    expect(slot(slotId).messages[3].metadata?.custom?.status).toBe('stopped');
  });

  it('with the server out of reach keeps what streamed as stopped, and asks for nothing more', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    srv.down = true;
    const run = streamMessageForSlot(slotId, Q, request());
    await pass(4_000);
    cancelStreamForSlot(slotId);
    await pass(5_000);
    await run;
    expect(slot(slotId).isStreaming).toBe(false);
    expect(texts(slotId).slice(2)).toEqual([['user', Q], ['assistant', HALF]]);
    expect(slot(slotId).messages[3].metadata?.custom?.status).toBe('stopped');
    const asked = srv.checks();
    await pass(60_000);
    expect(srv.checks()).toBe(asked);
  });

  it('then a new question: the first run is let go of, and never settles the second', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.asked();
    const first = streamMessageForSlot(slotId, Q, request());
    await pass(4_000);
    cancelStreamForSlot(slotId);
    respond({ chunks: [frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'Higher' })], hang: true });
    const second = streamMessageForSlot(slotId, 'And Q4?', request({ query: 'And Q4?' }));
    await pass(0);
    await first;
    srv.answered();
    const asked = srv.checks();
    await pass(10_000);
    expect(srv.checks()).toBe(asked);
    expect(slot(slotId)).toMatchObject({ isStreaming: true, streamingContent: 'Higher' });
    slot(slotId).abortController?.abort();
    await second;
  });
});

describe('a regenerate whose stream drops', () => {
  it('stays the answer being written, not the old one, and ends as the new one', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.status = 'Inprogress';
    const run = streamRegenerateForSlot(slotId, 'a1', MODEL);
    await pass(9_000);
    expect(slot(slotId)).toMatchObject({ isStreaming: true, regenerateMessageId: 'a1', streamingContent: HALF, remoteRunInProgress: false });

    srv.rows[1] = stored({ ...EARLIER[1], content: 'Hello again.', updatedAt: '2026-09-18T00:01:00.000Z' });
    srv.status = 'Complete';
    await pass(3_000);
    await run;
    expect(slot(slotId)).toMatchObject({ isStreaming: false, regenerateMessageId: null });
    expect(texts(slotId)).toEqual([['user', 'Hello'], ['assistant', 'Hello again.']]);
    expect(toasts()).toEqual([]);
  });

  it('that the server has nothing of puts the old answer back and says so', async () => {
    server(EARLIER);
    const slotId = openChat();
    drops();
    const run = streamRegenerateForSlot(slotId, 'a1', MODEL);
    await pass(GIVE_UP_AFTER_MS - 100);
    expect(slot(slotId).regenerateMessageId).toBe('a1');
    expect(toasts()).toEqual([]);
    await pass(200);
    await run;
    expect(slot(slotId)).toMatchObject({ isStreaming: false, regenerateMessageId: null });
    expect(texts(slotId)).toEqual([['user', 'Hello'], ['assistant', 'Hi there.']]);
    expect(toasts()).toEqual([CHAT_STREAM_ERROR_MESSAGES.interrupted]);
  });

  it('that failed on the server shows the stored failure in the answer', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    const run = streamRegenerateForSlot(slotId, 'a1', MODEL);
    srv.rows[1] = stored({ ...EARLIER[1], messageType: 'error', content: 'It timed out.', updatedAt: '2026-09-18T00:01:00.000Z' });
    await pass(1_500);
    await run;
    expect(texts(slotId)).toEqual([['user', 'Hello'], ['assistant', 'It timed out.']]);
    expect(failedRows(slotId)).toBe(1);
  });

  it('Stop while it is asked for stops the run on the server', async () => {
    const srv = server(EARLIER);
    const slotId = openChat();
    drops();
    srv.status = 'Inprogress';
    const run = streamRegenerateForSlot(slotId, 'a1', MODEL);
    await pass(4_000);
    const { runId } = slot(slotId);
    cancelStreamForSlot(slotId);
    await pass(0);
    expect(srv.cancels).toEqual([{ runId }]);
    srv.rows[1] = stored({ ...EARLIER[1], content: HALF, status: 'stopped', updatedAt: '2026-09-18T00:01:00.000Z' });
    srv.status = 'Stopped';
    await pass(1_000);
    await run;
    expect(texts(slotId)).toEqual([['user', 'Hello'], ['assistant', HALF]]);
    expect(slot(slotId).isStreaming).toBe(false);
  });

  describe('with an edited question', () => {
    const EDIT = { query: 'Hello, you' };

    it('keeps the new words on screen, and ends as the new answer to them', async () => {
      const srv = server(EARLIER);
      const slotId = openChat();
      drops();
      srv.status = 'Inprogress';
      const run = streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, EDIT);
      await pass(6_000);
      expect(slot(slotId)).toMatchObject({ isStreaming: true, regenerateMessageId: 'a1' });
      expect(texts(slotId)[0]).toEqual(['user', 'Hello, you']);

      srv.rows = [
        stored({ ...EARLIER[0], content: 'Hello, you', updatedAt: '2026-09-18T00:01:00.000Z' }),
        stored({ ...EARLIER[1], content: 'Hello again.', updatedAt: '2026-09-18T00:01:00.000Z' }),
      ];
      srv.status = 'Complete';
      await pass(3_000);
      await run;
      expect(texts(slotId)).toEqual([['user', 'Hello, you'], ['assistant', 'Hello again.']]);
      expect(slot(slotId).composerRestore ?? null).toBeNull();
    });

    it('that the server never took puts the old question back, and the new words in the box', async () => {
      server(EARLIER);
      const slotId = openChat();
      drops();
      const run = streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, EDIT);
      await pass(GIVE_UP_AFTER_MS + 100);
      await run;
      expect(texts(slotId)).toEqual([['user', 'Hello'], ['assistant', 'Hi there.']]);
      expect(slot(slotId).composerRestore).toBe('Hello, you');
    });

    it('whose run failed there shows what the server kept, and the new words go back to the box', async () => {
      const srv = server(EARLIER);
      const slotId = openChat();
      drops();
      const run = streamRegenerateForSlot(slotId, 'a1', MODEL, undefined, EDIT);
      srv.rows[1] = stored({ ...EARLIER[1], messageType: 'error', content: 'It timed out.', updatedAt: '2026-09-18T00:01:00.000Z' });
      await pass(1_500);
      await run;
      expect(texts(slotId)).toEqual([['user', 'Hello'], ['assistant', 'It timed out.']]);
      expect(slot(slotId).composerRestore).toBe('Hello, you');
      expect(toasts()).toEqual(["Your edit wasn't saved. Try again."]);
    });
  });
});

describe('the answer to a card whose stream drops', () => {
  const CARD = {
    name: 'ask_user_question' as const,
    questions: [
      { uuid: 'q-region', question: 'Which region?', multiSelect: false, options: [{ id: 'o-eu', label: 'EU', isUserInput: false }, { id: 'o-us', label: 'US', isUserInput: false }] },
    ],
  };
  const SELECTIONS = 'User selections:\n1. "Which region?" → EU';
  const CARD_ROWS = [
    stored({ _id: 'u1', content: 'Plan the launch' }),
    stored({ _id: 't1', messageType: 'tool_call', tools: [{ toolName: 'ask_user_question', toolResult: CARD }] } as Partial<ConversationMessage>),
    stored({ _id: 'b1', messageType: 'bot_response' }),
  ];
  const answers = { 'q-region': { questionUuid: 'q-region', selectedOptionIds: ['o-eu'], userInputs: {} } };

  /** The card on screen as it was submitted: closed, its answers on their way. */
  function submitted(): string {
    const slotId = openChat(CARD_ROWS);
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: { assistantMessageId: 'b1', payload: CARD, answers, status: 'submitted' } as unknown as PendingAskUserQuestion,
    });
    return slotId;
  }
  const card = (id: string) => slot(id).pendingAskUserQuestion;
  const answersOn = (id: string) =>
    (slot(id).messages.find((m) => m.id === 'b1')?.metadata?.custom as { persistedAskUserQuestionAnswers?: unknown }).persistedAskUserQuestionAnswers;
  const submit = (slotId: string) => streamMessageForSlot(slotId, SELECTIONS, request({ query: SELECTIONS }), { resumeAskUserQuestion: true });

  it('keeps the card answered, with nothing to submit again, and ends as the answer', async () => {
    const srv = server(CARD_ROWS);
    const slotId = submitted();
    drops([]);
    const run = submit(slotId);
    srv.asked(SELECTIONS, 'sel');
    await pass(9_000);
    expect(slot(slotId).isStreaming).toBe(true);
    expect(card(slotId)?.status).toBe('submitted');

    srv.answered('Planning for the EU.');
    await pass(3_000);
    await run;
    expect(slot(slotId).isStreaming).toBe(false);
    expect(texts(slotId)).toEqual([['user', 'Plan the launch'], ['assistant', 'Planning for the EU.']]);
    expect(card(slotId)?.status).toBe('submitted');
    expect(answersOn(slotId)).toEqual(answers);
    expect(toasts()).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('whose run had ended by then takes the answer from the rows', async () => {
    const srv = server(CARD_ROWS);
    const slotId = submitted();
    drops([]);
    srv.asked(SELECTIONS, 'sel');
    srv.answered('Planning for the EU.');
    const run = submit(slotId);
    await pass(100);
    await run;
    expect(texts(slotId)).toEqual([['user', 'Plan the launch'], ['assistant', 'Planning for the EU.']]);
    expect(card(slotId)?.status).toBe('submitted');
  });

  it('that asked its next card opens that one', async () => {
    const srv = server(CARD_ROWS);
    const slotId = submitted();
    drops([]);
    const next = { name: 'ask_user_question', questions: [{ uuid: 'q-when', question: 'When?', multiSelect: false, options: [{ id: 'o-now', label: 'Now', isUserInput: false }] }] };
    srv.asked(SELECTIONS, 'sel');
    srv.rows.push(stored({ _id: 't2', messageType: 'tool_call', tools: [{ toolName: 'ask_user_question', toolResult: next }] } as Partial<ConversationMessage>));
    srv.answered('', { _id: 'b2' });
    const run = submit(slotId);
    await pass(100);
    await run;
    expect(card(slotId)).toMatchObject({ status: 'pending', assistantMessageId: 'b1' });
    expect(card(slotId)?.payload.questions.map((q) => q.question)).toEqual(['When?']);
  });

  it('that the server has nothing of opens the card again, with what was picked, and says so', async () => {
    server(CARD_ROWS);
    const slotId = submitted();
    drops([]);
    const run = submit(slotId);
    await pass(GIVE_UP_AFTER_MS - 100);
    expect(card(slotId)?.status).toBe('submitted');
    await pass(200);
    await run;
    expect(card(slotId)).toMatchObject({ status: 'pending', answers });
    expect(toasts()).toHaveLength(1);
    expect(failedRows(slotId)).toBe(0);
  });

  describe('an approval card', () => {
    const WAITING: ConversationMessage[] = [
      stored({ _id: 'u1', content: 'File a bug' }),
      stored({ _id: 'b1', messageType: 'bot_response', content: 'I will file it.', parts: [{ type: 'tool_call', toolCallId: 'c1', toolName: 'create_issue', status: 'awaiting_approval' }] } as Partial<ConversationMessage>),
    ];
    const allow = (slotId: string) =>
      streamMessageForSlot(slotId, 'Tool approval: allow', request({ query: 'Tool approval: allow', toolApproval: { approvalId: 'ap-1', decision: 'allow' } }), { reuseRowId: 'b1' });

    it('is followed, and its result joins the row', async () => {
      const srv = server(WAITING);
      const slotId = openChat(WAITING);
      drops([]);
      const run = allow(slotId);
      srv.asked('Tool approval: allow', 'ok');
      await pass(6_000);
      expect(slot(slotId).isStreaming).toBe(true);
      srv.answered('Filed as JIRA-7.', { parts: [{ type: 'tool_call', toolCallId: 'c1', toolName: 'create_issue', status: 'completed', approved: true }, { type: 'text', content: 'Filed as JIRA-7.', isFinal: true }] });
      await pass(3_000);
      await run;
      expect(texts(slotId)).toEqual([['user', 'File a bug'], ['assistant', 'Filed as JIRA-7.']]);
      expect(toasts()).toEqual([]);
    });

    it('that the server has nothing of can be answered again, and says so', async () => {
      server(WAITING);
      const slotId = openChat(WAITING);
      drops([]);
      const run = allow(slotId);
      await pass(GIVE_UP_AFTER_MS + 100);
      await run;
      expect(slot(slotId).isStreaming).toBe(false);
      expect(texts(slotId)).toEqual([['user', 'File a bug'], ['assistant', 'I will file it.']]);
      expect(toasts()).toHaveLength(1);
    });
  });
});

describe('what the rows say of a run', () => {
  const own = (row: ConversationMessage) => row._id === 'mine';
  const mine = stored({ _id: 'mine', content: Q });
  const reply = stored({ _id: 'r', messageType: 'bot_response', content: WHOLE });

  it.each<[string, ConversationMessage[], boolean, string | null]>([
    ['not stored', EARLIER, false, null],
    ['not stored, and the chat is busy with another run', EARLIER, true, null],
    ['stored, and the chat has a run going', [...EARLIER, mine], true, 'running'],
    ['stored, and nothing runs', [...EARLIER, mine], false, null],
    ['answered', [...EARLIER, mine, reply], false, 'ended'],
    ['answered, and the chat has gone on to another run', [...EARLIER, mine, reply, stored({ _id: 'next' })], true, 'ended'],
    ['failed', [...EARLIER, mine, { ...reply, messageType: 'error' }], false, 'ended'],
    ['stored, with a card the run asked before its answer', [...EARLIER, mine, { ...reply, messageType: 'tool_call' }], true, 'running'],
    ['followed by another question with no answer between', [...EARLIER, mine, stored({ _id: 'next' })], true, null],
    ['stored as a message that asked nobody', [...EARLIER, { ...mine, messageType: 'note' } as unknown as ConversationMessage], false, 'ended'],
  ])('a send: %s', (_name, rows, inProgress, state) => {
    expect(sendOnServer(rows, own, inProgress)).toBe(state);
  });

  it('a turn as it was read takes its place among the rows held, in its own order', () => {
    const rows = (...ids: string[]) => ids.map((_id) => stored({ _id }));
    const ids = (list: ConversationMessage[]) => list.map((m) => m._id);
    // The answer came before its question did.
    expect(ids(withReadRows(rows('o', 'r'), rows('q', 'r')))).toEqual(['o', 'q', 'r']);
    expect(ids(withReadRows(rows('o', 'r', 'later'), rows('q', 'r')))).toEqual(['o', 'q', 'r', 'later']);
    expect(ids(withReadRows(rows('o', 'q'), rows('q', 'r')))).toEqual(['o', 'q', 'r']);
    expect(ids(withReadRows(rows('o'), rows('q', 'r')))).toEqual(['o', 'q', 'r']);
    expect(ids(withReadRows([], rows('q', 'r')))).toEqual(['q', 'r']);
  });

  const before = stored({ _id: 'a1', messageType: 'bot_response', content: 'old', runId: 'run-0' });
  const rewritten = { ...before, content: 'new', updatedAt: '2026-09-18T00:05:00.000Z' };
  it.each<[string, ConversationMessage[], Partial<Parameters<typeof regenerationOnServer>[1]>, boolean, string | null]>([
    ['written anew by this run, another going', [{ ...rewritten, runId: 'run-1' }], {}, true, 'ended'],
    ['still running', [before], {}, true, 'running'],
    ['over, with the answer written anew', [{ ...rewritten, runId: undefined }], {}, false, 'ended'],
    ['over, with the answer as it was', [before], {}, false, null],
    ['over, with no copy of the answer to compare: the server took it', [before], { before: undefined }, false, 'ended'],
    ['over, with no copy of the answer to compare: it may never have arrived', [before], { before: undefined, started: false }, false, null],
    ['of an answer that is gone', [], {}, false, null],
  ])('a regenerate: %s', (_name, rows, over, inProgress, state) => {
    expect(regenerationOnServer(rows, { messageId: 'a1', runId: 'run-1', before, started: true, ...over }, inProgress)).toBe(state);
  });
});

it('keeps rows other than the turn as they are', async () => {
  // A turn only this tab has (stopped before the server saved it) stays where it is.
  const srv = server(EARLIER);
  const slotId = openChat();
  const local: ThreadMessageLike[] = [
    { role: 'user', content: [{ type: 'text', text: 'Only here' }] },
    { id: 'local', role: 'assistant', content: [{ type: 'text', text: 'Stopped here' }], metadata: { custom: { status: 'stopped' } } },
  ];
  useChatStore.getState().updateSlot(slotId, { messages: [...slot(slotId).messages, ...local] });
  drops();
  srv.asked();
  srv.answered();
  const run = streamMessageForSlot(slotId, Q, request());
  await pass(100);
  await run;
  expect(texts(slotId)).toEqual([
    ['user', 'Hello'], ['assistant', 'Hi there.'], ['user', 'Only here'], ['assistant', 'Stopped here'], ['user', Q], ['assistant', WHOLE],
  ]);
});
