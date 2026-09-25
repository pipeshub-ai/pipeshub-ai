/**
 * A chat turn end to end: `streamMessageForSlot` → `ChatApi.streamMessage` →
 * the SSE transport → the AG-UI handler → the slot in the chat store. Only
 * `fetch` is faked, so these cover what a person sees in the thread while an
 * answer streams, when it finishes, and when it fails.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  installMemoryStorage,
  jsonResponse,
  jwtExpiringIn,
  sseFrame,
  sseResponse,
  type StreamPlan,
} from '@/lib/api/__tests__/sse-response';
import type { StreamChatRequest, ConversationMessage } from '../types';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useChatStore } = await import('../store');
const { streamMessageForSlot, streamRegenerateForSlot, loadOlderMessagesForSlot, cancelStreamForSlot } = await import('../streaming');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { CHAT_STREAM_ERROR_MESSAGES, busyStreamMessage } = await import('@/lib/api/stream-errors');
const { getThreadMessagePlainText } = await import('../runtime');
const { apiClient } = await import('@/lib/api');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();

function frame(type: string, fields: Record<string, unknown> = {}): string {
  return sseFrame(type, { type, ...fields });
}

function storedMessage(overrides: Partial<ConversationMessage>): ConversationMessage {
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

function finishedConversation(answer: string) {
  return {
    _id: 'conv-real',
    title: 'Quarterly revenue',
    createdAt: '2026-09-18T00:00:00.000Z',
    updatedAt: '2026-09-18T00:00:00.000Z',
    isShared: false,
    status: 'complete',
    modelInfo: { modelKey: 'm1', modelName: 'gpt-5' },
    messages: [
      storedMessage({ _id: 'q1', messageType: 'user_query', content: 'What was Q3 revenue?' }),
      storedMessage({ _id: 'a1', messageType: 'bot_response', content: answer }),
    ],
  };
}

const Q = 'What was Q3 revenue?';

function request(overrides: Partial<StreamChatRequest> = {}): StreamChatRequest {
  return {
    query: 'What was Q3 revenue?',
    modelKey: 'm1',
    modelName: 'gpt-5',
    modelFriendlyName: 'GPT-5',
    chatMode: 'quick',
    filters: { apps: [], kb: [] },
    ...overrides,
  } as StreamChatRequest;
}

function newSlot(convId: string | null = null): string {
  const slotId = useChatStore.getState().createSlot(convId);
  useChatStore.setState({ activeSlotId: slotId });
  return slotId;
}

const slot = (id: string) => useChatStore.getState().slots[id];
const texts = (id: string) => slot(id).messages.map((m) => [m.role, getThreadMessagePlainText(m)]);

function respondWith(plan: StreamPlan | string[] | Response) {
  if (plan instanceof Response) {
    fetchMock.mockResolvedValueOnce(plan);
    return;
  }
  const p = Array.isArray(plan) ? { chunks: plan } : plan;
  fetchMock.mockImplementationOnce(async (_url, init) => sseResponse({ ...p, signal: init?.signal }));
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

describe('a new conversation, start to finish', () => {
  it('shows the question and a placeholder at once, then the saved thread when the run finishes', async () => {
    const slotId = newSlot();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    fetchMock.mockImplementationOnce(async () => {
      await gate;
      return sseResponse([
        frame('CUSTOM', { name: 'conversation_created', value: { conversationId: 'conv-real', title: 'Quarterly revenue' } }),
        frame('TEXT_MESSAGE_START'),
        frame('TEXT_MESSAGE_CONTENT', { delta: 'Revenue was ' }),
        frame('TEXT_MESSAGE_CONTENT', { delta: '$4.2M.' }),
        frame('TEXT_MESSAGE_END'),
        frame('RUN_FINISHED', { result: { conversation: finishedConversation('Revenue was $4.2M.') } }),
      ]);
    });

    const run = streamMessageForSlot(slotId, Q, request());

    expect(slot(slotId).isStreaming).toBe(true);
    expect(texts(slotId)).toEqual([
      ['user', 'What was Q3 revenue?'],
      ['assistant', ''],
    ]);
    expect(useChatStore.getState().pendingConversations[slotId]).toBeDefined();

    release();
    await run;

    const s = slot(slotId);
    expect(s.isStreaming).toBe(false);
    expect(s.streamingContent).toBe('');
    expect(s.runId).toBeNull();
    expect(s.convId).toBe('conv-real');
    expect(texts(slotId)).toEqual([
      ['user', 'What was Q3 revenue?'],
      ['assistant', 'Revenue was $4.2M.'],
    ]);
    // The sidebar row moves from "generating" to the saved conversation.
    expect(useChatStore.getState().pendingConversations[slotId]).toBeUndefined();
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['conv-real']);
  });

  it('posts to the new-conversation endpoint with a run id that a Stop can target', async () => {
    const slotId = newSlot();
    respondWith([frame('RUN_FINISHED', { result: { conversation: finishedConversation('ok') } })]);
    await streamMessageForSlot(slotId, Q, request({ filters: { apps: ['', 'app-1'], kb: [] } }));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/conversations/stream');
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({ query: 'What was Q3 revenue?', protocol: 'agui', filters: { apps: ['app-1'], kb: [] } });
    expect(body.runId).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('streams answer text into the slot as it arrives', async () => {
    const slotId = newSlot();
    respondWith({
      chunks: [frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'Revenue ' })],
      hang: true,
    });
    const run = streamMessageForSlot(slotId, Q, request());

    await vi.waitFor(() => expect(slot(slotId).streamingContent).toBe('Revenue '));
    expect(slot(slotId).isStreaming).toBe(true);

    slot(slotId).abortController?.abort();
    await run;
  });
});

describe('an existing conversation', () => {
  it('posts to that conversation and moves it to the top of the sidebar', async () => {
    const slotId = newSlot('conv-old');
    useChatStore.setState({
      conversations: [
        { id: 'conv-other', title: 'Other' } as never,
        { id: 'conv-old', title: 'Old' } as never,
      ],
    });
    respondWith([frame('RUN_FINISHED', { result: { conversation: { ...finishedConversation('ok'), _id: 'conv-old' } } })]);

    await streamMessageForSlot(slotId, Q, request({ conversationId: 'conv-old' }));

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/conversations/conv-old/messages/stream');
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['conv-old', 'conv-other']);
    expect(slot(slotId).isStreaming).toBe(false);
  });
});

describe('when the answer fails', () => {
  it("replaces the empty placeholder with the server's error", async () => {
    const slotId = newSlot();
    respondWith([
      frame('RUN_ERROR', { message: 'The AI model is not responding right now. Try another model or try again later.', code: 'stream_error' }),
    ]);

    await streamMessageForSlot(slotId, Q, request());

    expect(texts(slotId)).toEqual([
      ['user', 'What was Q3 revenue?'],
      ['assistant', 'The AI model is not responding right now. Try another model or try again later.'],
    ]);
    expect(slot(slotId).isStreaming).toBe(false);
    expect(useChatStore.getState().pendingConversations[slotId]).toBeUndefined();
  });

  it('says PipesHub is busy when the request is rate limited', async () => {
    const slotId = newSlot();
    respondWith(jsonResponse(429, {}, { 'retry-after': '3' }));
    await streamMessageForSlot(slotId, Q, request());
    expect(texts(slotId)[1]).toEqual(['assistant', busyStreamMessage(3)]);
  });

  it('keeps no half-answer and says it was interrupted when the connection drops', async () => {
    const slotId = newSlot();
    respondWith({
      chunks: [frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'Revenue was' })],
      failWith: new TypeError('network error'),
    });

    await streamMessageForSlot(slotId, Q, request());

    const s = slot(slotId);
    expect(s.isStreaming).toBe(false);
    expect(s.streamingContent).toBe('');
    expect(texts(slotId)[1]).toEqual(['assistant', CHAT_STREAM_ERROR_MESSAGES.interrupted]);
  });

  it('says the answer was interrupted when the connection closes without finishing', async () => {
    // A proxy can end the response cleanly mid-answer. Before this was
    // handled, the turn stayed "streaming" forever: Stop showing, no answer,
    // and the sidebar entry stuck on "generating".
    const slotId = newSlot();
    respondWith([frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'Revenue was' })]);

    await streamMessageForSlot(slotId, Q, request());

    const s = slot(slotId);
    expect(s.isStreaming).toBe(false);
    expect(s.runId).toBeNull();
    expect(texts(slotId)[1]).toEqual(['assistant', CHAT_STREAM_ERROR_MESSAGES.interrupted]);
    expect(useChatStore.getState().pendingConversations[slotId]).toBeUndefined();
  });

  it('does not report an interruption for a run the person stopped', async () => {
    const slotId = newSlot();
    respondWith({ chunks: [frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'Rev' })], hang: true });
    const run = streamMessageForSlot(slotId, Q, request());
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toBe('Rev'));
    slot(slotId).abortController?.abort();
    await run;
    expect(texts(slotId).map(([, text]) => text)).not.toContain(CHAT_STREAM_ERROR_MESSAGES.interrupted);
  });

  it('ends the turn as interrupted when the server aborts a run nobody stopped', async () => {
    // Before, this frame counted as a finished turn, so with no Stop (and no
    // grace timer) the turn stayed streaming: Stop showing, sidebar "generating".
    const slotId = newSlot();
    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Revenue was' }),
      frame('RUN_ERROR', { message: 'aborted', code: 'abort' }),
    ]);

    await streamMessageForSlot(slotId, Q, request());

    const s = slot(slotId);
    expect(s.isStreaming).toBe(false);
    expect(s.stopping).toBe(false);
    expect(s.runId).toBeNull();
    expect(s.streamingContent).toBe('');
    expect(texts(slotId)).toEqual([
      ['user', Q],
      ['assistant', CHAT_STREAM_ERROR_MESSAGES.interrupted],
    ]);
    expect(useChatStore.getState().pendingConversations[slotId]).toBeUndefined();
  });
});

describe('Stop, when the stream then closes without a terminal frame', () => {
  /** A body the test closes by hand, the way a server ends a response. */
  function openBody(...frames: string[]) {
    let close!: (...lastFrames: string[]) => void;
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const f of frames) controller.enqueue(encoder.encode(f));
        close = (...lastFrames) => {
          for (const f of lastFrames) controller.enqueue(encoder.encode(f));
          controller.close();
        };
      },
    });
    fetchMock.mockResolvedValueOnce(new Response(body, { status: 200 }));
    return (...lastFrames: string[]) => close(...lastFrames);
  }

  afterEach(() => {
    vi.useRealTimers();
  });

  // Stop only posts a cancel and waits STOP_GRACE_MS before aborting, so
  // the request signal is still live when the connection closes.
  it('keeps the partial answer as a stopped reply, with no interrupted error', async () => {
    fakeApi({ 'POST /api/v1/conversations/conv-1/cancel': { status: 200, data: { cancelled: true } } });
    const slotId = newSlot('conv-1');
    const close = openBody(frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'Revenue was' }));
    const run = streamMessageForSlot(slotId, Q, request({ conversationId: 'conv-1' }));
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toBe('Revenue was'));

    vi.useFakeTimers();
    cancelStreamForSlot(slotId);
    expect(slot(slotId).stopping).toBe(true);
    close();
    await run;
    await vi.advanceTimersByTimeAsync(5_000);

    const s = slot(slotId);
    expect(s.isStreaming).toBe(false);
    expect(texts(slotId)).toEqual([
      ['user', Q],
      ['assistant', 'Revenue was'],
    ]);
    expect(s.messages[1].metadata?.custom?.status).toBe('stopped');
  });

  it('keeps the partial answer as a stopped reply when the Stop comes back as an abort RUN_ERROR', async () => {
    fakeApi({ 'POST /api/v1/conversations/conv-1/cancel': { status: 200, data: { cancelled: true } } });
    const slotId = newSlot('conv-1');
    const close = openBody(frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'Revenue was' }));
    const run = streamMessageForSlot(slotId, Q, request({ conversationId: 'conv-1' }));
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toBe('Revenue was'));

    vi.useFakeTimers();
    cancelStreamForSlot(slotId);
    close(frame('RUN_ERROR', { message: 'aborted', code: 'abort' }));
    await run;
    expect(slot(slotId).stopping).toBe(true);
    expect(texts(slotId).map(([, text]) => text)).not.toContain(CHAT_STREAM_ERROR_MESSAGES.interrupted);

    await vi.advanceTimersByTimeAsync(5_000);

    const s = slot(slotId);
    expect(s.isStreaming).toBe(false);
    expect(s.stopping).toBe(false);
    expect(texts(slotId)).toEqual([
      ['user', Q],
      ['assistant', 'Revenue was'],
    ]);
    expect(s.messages[1].metadata?.custom?.status).toBe('stopped');
  });

  it('keeps the partial regenerated answer as a stopped reply', async () => {
    fakeApi({ 'POST /api/v1/conversations/conv-1/cancel': { status: 200, data: { cancelled: true } } });
    const slotId = newSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, {
      messages: [
        { id: 'q1', role: 'user', content: [{ type: 'text', text: 'Q' }] },
        { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'old answer' }] },
      ],
    });
    const close = openBody(frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'new partial' }));
    const run = streamRegenerateForSlot(slotId, 'a1', { modelKey: 'm1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' });
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toBe('new partial'));

    vi.useFakeTimers();
    cancelStreamForSlot(slotId);
    close();
    await run;
    await vi.advanceTimersByTimeAsync(5_000);

    const s = slot(slotId);
    expect(s.isStreaming).toBe(false);
    expect(s.regenerateMessageId).toBeNull();
    expect(texts(slotId)[1]).toEqual(['assistant', 'new partial']);
    expect(s.messages[1].metadata?.custom?.status).toBe('stopped');
  });
});

describe('events from a run that is no longer current', () => {
  it('cannot write into the slot after a newer run starts', async () => {
    const slotId = newSlot();
    let releaseFirst!: () => void;
    const firstGate = new Promise<void>((r) => (releaseFirst = r));
    fetchMock.mockImplementationOnce(async () => {
      await firstGate;
      return sseResponse([
        frame('TEXT_MESSAGE_START'),
        frame('TEXT_MESSAGE_CONTENT', { delta: 'STALE' }),
        frame('RUN_FINISHED', { result: { conversation: finishedConversation('STALE') } }),
      ]);
    });
    const first = streamMessageForSlot(slotId, 'first', request());

    respondWith([frame('RUN_FINISHED', { result: { conversation: finishedConversation('fresh answer') } })]);
    await streamMessageForSlot(slotId, 'second', request());
    releaseFirst();
    await first;

    expect(texts(slotId)).toEqual([
      ['user', 'What was Q3 revenue?'],
      ['assistant', 'fresh answer'],
    ]);
    expect(slot(slotId).streamingContent).toBe('');
  });
});

describe('asking the user a question mid-run', () => {
  it('drops the partial answer and parks the question on the placeholder row', async () => {
    const slotId = newSlot();
    respondWith({
      chunks: [
        frame('TEXT_MESSAGE_START'),
        frame('TEXT_MESSAGE_CONTENT', { delta: 'Let me check' }),
        frame('CUSTOM', {
          name: 'ask_user_question',
          value: { toolData: { name: 'ask_user_question', questions: [{ question: 'Which region?', options: ['EU', 'US'] }] } },
        }),
        frame('TEXT_MESSAGE_CONTENT', { delta: ' ignored' }),
      ],
      hang: true,
    });
    const run = streamMessageForSlot(slotId, Q, request());
    await vi.waitFor(() => expect(slot(slotId).pendingAskUserQuestion).toBeTruthy());

    const s = slot(slotId);
    const placeholderId = s.messages[s.messages.length - 1].id;
    expect(s.pendingAskUserQuestion).toMatchObject({ assistantMessageId: placeholderId, status: 'pending' });
    expect(s.streamingContent).toBe('');

    slot(slotId).abortController?.abort();
    await run;
  });
});

describe('regenerating an answer', () => {
  it('reloads the conversation when the new answer is saved', async () => {
    const slotId = newSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, {
      messages: [
        { id: 'q1', role: 'user', content: [{ type: 'text', text: 'Q' }] },
        { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'old answer' }] },
      ],
    });
    respondWith([frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'new' }), frame('RUN_FINISHED', { result: {} })]);
    const get = vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: { conversation: { ...finishedConversation('new answer'), id: 'conv-1' } },
    });

    await streamRegenerateForSlot(slotId, 'a1', { modelKey: 'm1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' });

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/conversations/conv-1/message/a1/regenerate');
    expect(get.mock.calls[0][0]).toBe('/api/v1/conversations/conv-1/');
    const s = slot(slotId);
    expect(s.isStreaming).toBe(false);
    expect(s.regenerateMessageId).toBeNull();
    expect(texts(slotId)[1]).toEqual(['assistant', 'new answer']);
  });

  it('leaves the previous answer in place when the regenerate request fails', async () => {
    const slotId = newSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, {
      messages: [
        { id: 'q1', role: 'user', content: [{ type: 'text', text: 'Q' }] },
        { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'old answer' }] },
      ],
    });
    respondWith(jsonResponse(500, {}));
    await streamRegenerateForSlot(slotId, 'a1', { modelKey: 'm1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' });
    const s = slot(slotId);
    expect(s.isStreaming).toBe(false);
    expect(s.regenerateMessageId).toBeNull();
    expect(texts(slotId)[1]).toEqual(['assistant', 'old answer']);
  });
});

describe('loading older messages', () => {
  it('prepends only messages the thread does not already have', async () => {
    const slotId = newSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, {
      messages: [{ id: 'q2', role: 'user', content: [{ type: 'text', text: 'newer' }] }],
      messagePagination: { currentPage: 1, hasOlderMessages: true, isLoadingOlder: false },
    });
    const get = vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: {
        conversation: {
          messages: [storedMessage({ _id: 'q1', content: 'older' }), storedMessage({ _id: 'q2', content: 'newer' })],
          pagination: { page: 2, limit: 20, totalCount: 3, totalPages: 2, hasNextPage: false, hasPrevPage: true },
        },
      },
    });

    await loadOlderMessagesForSlot(slotId);

    expect(get.mock.calls[0][1]).toMatchObject({ params: { page: 2 } });
    expect(slot(slotId).messages.map((m) => m.id)).toEqual(['q1', 'q2']);
    expect(slot(slotId).messagePagination).toEqual({ currentPage: 2, hasOlderMessages: false, isLoadingOlder: false });
  });

  it('lets the next scroll try again after a failed load', async () => {
    const slotId = newSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, {
      messagePagination: { currentPage: 1, hasOlderMessages: true, isLoadingOlder: false },
    });
    vi.spyOn(apiClient, 'get').mockRejectedValueOnce(new Error('offline'));
    await loadOlderMessagesForSlot(slotId);
    expect(slot(slotId).messagePagination).toEqual({ currentPage: 1, hasOlderMessages: true, isLoadingOlder: false });
  });
});
