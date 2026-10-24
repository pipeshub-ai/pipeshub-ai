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
import type { StreamChatRequest, ConversationMessage, MessagePart } from '../types';

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
const { getThreadMessagePlainText, loadHistoricalMessages } = await import('../runtime');
const { apiClient } = await import('@/lib/api');
const { useToastStore } = await import('@/lib/store/toast-store');

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
  useToastStore.getState().clearAll();
  // The chat only ever runs on /chat; whether a finished answer is "in view" depends on it.
  window.history.replaceState(null, '', '/chat/');
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

  it('ignores an artifact event with no payload instead of failing the run', async () => {
    const slotId = newSlot();
    respondWith([
      frame('CUSTOM', { name: 'artifact' }),
      frame('RUN_FINISHED', { result: { conversation: finishedConversation('ok') } }),
    ]);
    await streamMessageForSlot(slotId, Q, request());

    expect(slot(slotId).error).toBeFalsy();
    expect(texts(slotId)).toContainEqual(['assistant', 'ok']);
  });

  it('settles the turn and reloads the saved thread when a new message finishes without the conversation', async () => {
    const slotId = newSlot('conv-1');
    respondWith([frame('TEXT_MESSAGE_START'), frame('RUN_FINISHED', { result: {} })]);
    const get = vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: { conversation: { ...finishedConversation('saved answer'), id: 'conv-1' } },
    });
    await streamMessageForSlot(slotId, Q, request());

    await vi.waitFor(() => expect(texts(slotId)).toContainEqual(['assistant', 'saved answer']));
    expect(get.mock.calls[0][0]).toBe('/api/v1/conversations/conv-1/');
    expect(slot(slotId).error).toBeFalsy();
    expect(slot(slotId).isStreaming).toBe(false);
  });

  it('keeps a newer turn when the reload of an earlier settled turn arrives late', async () => {
    const slotId = newSlot('conv-1');
    respondWith([frame('TEXT_MESSAGE_START'), frame('RUN_FINISHED', { result: {} })]);
    let deliverReload: (value: unknown) => void = () => {};
    vi.spyOn(apiClient, 'get').mockReturnValueOnce(new Promise((resolve) => { deliverReload = resolve; }));
    await streamMessageForSlot(slotId, Q, request());

    respondWith([frame('RUN_FINISHED', { result: { conversation: { ...finishedConversation('newer answer'), _id: 'conv-1' } } })]);
    await streamMessageForSlot(slotId, 'And Q4?', request({ query: 'And Q4?' }));
    expect(texts(slotId)).toContainEqual(['assistant', 'newer answer']);

    deliverReload({ data: { conversation: { ...finishedConversation('stale answer'), id: 'conv-1' } } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(texts(slotId)).toContainEqual(['assistant', 'newer answer']);
    expect(texts(slotId)).not.toContainEqual(['assistant', 'stale answer']);
  });

  it('keeps the streamed answer and drops the generating row when that reload fails', async () => {
    const slotId = newSlot();
    respondWith([
      frame('CUSTOM', { name: 'conversation_created', value: { conversationId: 'conv-1', title: 'Quarterly revenue' } }),
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Revenue was $4.2M.' }),
      frame('RUN_FINISHED', { result: {} }),
    ]);
    vi.spyOn(apiClient, 'get').mockRejectedValueOnce(new Error('offline'));
    await streamMessageForSlot(slotId, Q, request());

    await vi.waitFor(() => expect(useChatStore.getState().pendingConversations[slotId]).toBeUndefined());
    expect(texts(slotId)).toContainEqual(['assistant', 'Revenue was $4.2M.']);
    expect(slot(slotId).convId).toBe('conv-1');
    expect(slot(slotId).isTemp).toBe(false);
  });

  it('files the new chat in the sidebar and restarts paging from the reloaded page', async () => {
    const slotId = newSlot();
    respondWith([
      frame('CUSTOM', { name: 'conversation_created', value: { conversationId: 'conv-1', title: 'Quarterly revenue' } }),
      frame('RUN_FINISHED', { result: {} }),
    ]);
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: {
        conversation: {
          ...finishedConversation('saved answer'),
          id: 'conv-1',
          pagination: { page: 1, limit: 20, totalCount: 30, totalPages: 2, hasNextPage: true, hasPrevPage: false },
        },
      },
    });
    await streamMessageForSlot(slotId, Q, request());

    await vi.waitFor(() => expect(texts(slotId)).toContainEqual(['assistant', 'saved answer']));
    expect(useChatStore.getState().pendingConversations[slotId]).toBeUndefined();
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['conv-1']);
    expect(slot(slotId).messagePagination).toEqual({ currentPage: 1, hasOlderMessages: true, isLoadingOlder: false });
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

  it('sends a selection made only of a folder or a record group, and no empty keys', async () => {
    const slotId = newSlot();
    respondWith([frame('RUN_FINISHED', { result: { conversation: finishedConversation('ok') } })]);
    await streamMessageForSlot(
      slotId,
      Q,
      request({ filters: { apps: [], kb: [], recordGroups: [], records: ['folder-1'] } }),
    );

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.filters).toEqual({ apps: [], kb: [], records: ['folder-1'] });
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

  it("leaves an earlier turn's activity timeline alone when a later turn finishes", async () => {
    const slotId = newSlot('conv-real');
    const conversation = {
      ...finishedConversation('Second answer.'),
      messages: [
        storedMessage({ _id: 'q1', messageType: 'user_query', content: 'First question' }),
        storedMessage({
          _id: 'a1',
          messageType: 'bot_response',
          content: 'First answer.',
          parts: [{
            type: 'tool_call',
            toolCallId: 'old-call',
            toolName: 'confluence_search',
            status: 'completed',
          }],
        }),
        storedMessage({ _id: 'q2', messageType: 'user_query', content: Q }),
        storedMessage({ _id: 'a2', messageType: 'bot_response', content: 'Second answer.' }),
      ],
    };
    respondWith([
      frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'call-1', toolCallName: 'jira_search' }),
      frame('TOOL_CALL_RESULT', { runId: 'root', toolCallId: 'call-1', content: '3 issues', status: 'completed' }),
      frame('TEXT_MESSAGE_START', { runId: 'root' }),
      frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'Second answer.' }),
      frame('TEXT_MESSAGE_END', { runId: 'root' }),
      frame('RUN_FINISHED', { result: { conversation } }),
    ]);

    await streamMessageForSlot(slotId, Q, request({ conversationId: 'conv-real' }));

    const earlier = slot(slotId).messages.find((m) => m.id === 'a1');
    const earlierCustom = earlier?.metadata?.custom as { persistedParts?: MessagePart[] } | undefined;
    expect((earlierCustom?.persistedParts ?? []).map((p) => p.toolName)).toEqual(['confluence_search']);
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

  it('keeps the error code and details so the thread can offer a fix', async () => {
    const slotId = newSlot();
    const details = { agentId: 'a-1', serviceAccount: false, servers: [{ instanceId: 'inst-1', name: 'GitHub', problem: 'not_connected' }] };
    respondWith([frame('RUN_ERROR', { message: 'Connect GitHub first.', code: 'mcp_server_config_missing', details })]);

    await streamMessageForSlot(slotId, Q, request());

    const reply = slot(slotId).messages[1];
    expect(getThreadMessagePlainText(reply)).toBe('Connect GitHub first.');
    expect(reply.metadata?.custom?.streamError).toEqual({ code: 'mcp_server_config_missing', details });
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

  it('keeps the question card and says so when the resume stream fails', async () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{ question: 'Which region?', options: ['EU', 'US'] }],
    };
    const slotId = newSlot();
    respondWith([
      frame('CUSTOM', {
        name: 'ask_user_question',
        value: { toolData: payload },
      }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation(''),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              storedMessage({
                _id: 't1',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: payload }],
              }),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(slotId, Q, request());
    expect(slot(slotId).pendingAskUserQuestion?.status).toBe('pending');

    const answers = {
      q1: { questionUuid: 'q1', selectedOptionIds: ['eu'], userInputs: {} },
    };
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: {
        ...slot(slotId).pendingAskUserQuestion!,
        answers,
        status: 'submitted',
      },
    });

    respondWith([frame('RUN_ERROR', { message: 'offline' })]);
    await streamMessageForSlot(
      slotId,
      'User selections:\n1. "Which region?" → EU',
      request({ query: 'User selections:\n1. "Which region?" → EU' }),
      { resumeAskUserQuestion: true },
    );

    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({
      status: 'pending',
      answers,
    });
    // The card row's text is hidden while the card is pending, so the toast is
    // the only place the user learns the answers never reached the model.
    expect(
      useToastStore.getState().toasts.map((t) => ({ variant: t.variant, description: t.description })),
    ).toEqual([{ variant: 'error', description: 'offline' }]);
  });

  it('keeps the first ask pending and does not mark it submitted', async () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{ uuid: 'q-region', question: 'Which region?', options: [{ id: 'eu', label: 'EU' }] }],
    };
    const slotId = newSlot();
    respondWith([
      frame('CUSTOM', {
        name: 'ask_user_question',
        value: { toolData: payload },
      }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation(''),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              storedMessage({
                _id: 't1',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: payload }],
              }),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(slotId, Q, request());

    expect(slot(slotId).pendingAskUserQuestion?.status).toBe('pending');
    expect(texts(slotId)).toEqual([
      ['user', Q],
      ['assistant', ''],
    ]);
  });

  it('happy-path resume keeps the follow-up on the same card', async () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-region',
        question: 'Which region?',
        multiSelect: false,
        options: [
          { id: 'eu', label: 'EU', isUserInput: false },
          { id: 'us', label: 'US', isUserInput: false },
        ],
      }],
    };
    const slotId = newSlot();
    respondWith([
      frame('CUSTOM', {
        name: 'ask_user_question',
        value: { toolData: payload },
      }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation(''),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              storedMessage({
                _id: 't1',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: payload }],
              }),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(slotId, Q, request());
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: {
        ...slot(slotId).pendingAskUserQuestion!,
        answers: { 'q-region': { questionUuid: 'q-region', selectedOptionIds: ['eu'], userInputs: {} } },
        status: 'submitted',
      },
    });

    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'EU it is.' }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation('EU it is.'),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              storedMessage({
                _id: 't1',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: payload }],
              }),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
              storedMessage({
                _id: 'sel',
                messageType: 'user_query',
                content: 'User selections:\n1. "Which region?" → EU',
              }),
              storedMessage({ _id: 'a2', messageType: 'bot_response', content: 'EU it is.' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(
      slotId,
      'User selections:\n1. "Which region?" → EU',
      request({ query: 'User selections:\n1. "Which region?" → EU' }),
      { resumeAskUserQuestion: true },
    );

    expect(slot(slotId).pendingAskUserQuestion?.status).toBe('submitted');
    expect(texts(slotId)).toEqual([
      ['user', Q],
      ['assistant', 'EU it is.'],
    ]);
  });

  it('resume keeps live follow-up text when saved history is empty', async () => {
    const payload = {
      name: 'ask_user_question',
      questions: [{ uuid: 'q-region', question: 'Which region?', options: [{ id: 'eu', label: 'EU' }] }],
    };
    const slotId = newSlot();
    respondWith([
      frame('CUSTOM', {
        name: 'ask_user_question',
        value: { toolData: payload },
      }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation(''),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              storedMessage({
                _id: 't1',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: payload }],
              }),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(slotId, Q, request());
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: {
        ...slot(slotId).pendingAskUserQuestion!,
        answers: { 'q-region': { questionUuid: 'q-region', selectedOptionIds: ['eu'], userInputs: {} } },
        status: 'submitted',
      },
    });

    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Live follow-up stays.' }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation(''),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              storedMessage({
                _id: 't1',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: payload }],
              }),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
              storedMessage({
                _id: 'sel',
                messageType: 'user_query',
                content: 'User selections:\n1. "Which region?" → EU',
              }),
              storedMessage({ _id: 'a2', messageType: 'bot_response', content: '' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(
      slotId,
      'User selections:\n1. "Which region?" → EU',
      request({ query: 'User selections:\n1. "Which region?" → EU' }),
      { resumeAskUserQuestion: true },
    );

    expect(slot(slotId).pendingAskUserQuestion?.status).toBe('submitted');
    expect(texts(slotId)).toEqual([
      ['user', Q],
      ['assistant', 'Live follow-up stays.'],
    ]);
  });

  it('reopens the card when the resume ends on another question', async () => {
    const first = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-proceed',
        question: 'How would you like to proceed?',
        multiSelect: false,
        options: [{ id: 'kb', label: 'Search the knowledge base instead', isUserInput: false }],
      }],
    };
    const second = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q-topic',
        question: 'What topic should I search for?',
        multiSelect: false,
        options: [{ id: 'keywords', label: 'Enter a topic or keywords', isUserInput: false }],
      }],
    };
    const slotId = newSlot();
    respondWith([
      frame('CUSTOM', { name: 'ask_user_question', value: { toolData: first } }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation(''),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              storedMessage({
                _id: 't1',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: first }],
              }),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(slotId, Q, request());
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: {
        ...slot(slotId).pendingAskUserQuestion!,
        answers: { 'q-proceed': { questionUuid: 'q-proceed', selectedOptionIds: ['kb'], userInputs: {} } },
        status: 'submitted',
      },
    });

    const resumeQuery = 'User selections:\n1. "How would you like to proceed?" → Search the knowledge base instead';
    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: "I'll use the knowledge base." }),
      frame('CUSTOM', { name: 'ask_user_question', value: { toolData: second } }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation("I'll use the knowledge base."),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              storedMessage({
                _id: 't1',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: first }],
              }),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
              storedMessage({ _id: 'sel', messageType: 'user_query', content: resumeQuery }),
              storedMessage({
                _id: 'a2',
                messageType: 'bot_response',
                content: "I'll use the knowledge base.",
              }),
              storedMessage({
                _id: 't2',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: second }],
              }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(
      slotId,
      resumeQuery,
      request({ query: resumeQuery }),
      { resumeAskUserQuestion: true },
    );

    const pending = slot(slotId).pendingAskUserQuestion;
    expect(pending?.status).toBe('pending');
    expect(pending?.payload.questions.map((q) => q.uuid)).toEqual(['q-proceed', 'q-topic']);
    expect(pending?.answers).toEqual({
      'q-proceed': { questionUuid: 'q-proceed', selectedOptionIds: ['kb'], userInputs: {} },
    });

    // Answer the question that resume asked, and have that second resume come
    // back with nothing. The text the first resume left on the card must not
    // pass for this one's answer, or the card locks with no way to retry.
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: {
        ...slot(slotId).pendingAskUserQuestion!,
        answers: {
          'q-proceed': { questionUuid: 'q-proceed', selectedOptionIds: ['kb'], userInputs: {} },
          'q-topic': { questionUuid: 'q-topic', selectedOptionIds: ['keywords'], userInputs: {} },
        },
        status: 'submitted',
      },
    });
    const secondResumeQuery = 'User selections:\n1. "What topic should I search for?" → Enter a topic or keywords';
    respondWith([
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation(''),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              storedMessage({
                _id: 't1',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: first }],
              }),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
              storedMessage({ _id: 'sel', messageType: 'user_query', content: resumeQuery }),
              storedMessage({
                _id: 'a2',
                messageType: 'bot_response',
                content: "I'll use the knowledge base.",
              }),
              storedMessage({
                _id: 't2',
                messageType: 'tool_call',
                tools: [{ toolName: 'ask_user_question', toolResult: second }],
              }),
              storedMessage({ _id: 'sel2', messageType: 'user_query', content: secondResumeQuery }),
              storedMessage({ _id: 'a3', messageType: 'bot_response', content: '' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(
      slotId,
      secondResumeQuery,
      request({ query: secondResumeQuery }),
      { resumeAskUserQuestion: true },
    );

    expect(slot(slotId).pendingAskUserQuestion?.status).toBe('pending');
  });
});

describe('an agent that re-asks a question the user already answered', () => {
  const fiscalYear = {
    name: 'ask_user_question',
    questions: [{
      uuid: 'q-fy',
      question: 'Which fiscal year should I use?',
      multiSelect: false,
      options: [
        { id: 'fy24', label: 'FY2024', isUserInput: false },
        { id: 'latest', label: 'Most recent matching pair', isUserInput: false },
      ],
    }],
  };
  const picked = 'User selections:\n1. "Which fiscal year should I use?" → Most recent matching pair';
  const asked = (id: string) => storedMessage({
    _id: id,
    messageType: 'tool_call',
    tools: [{ toolName: 'ask_user_question', toolResult: fiscalYear }],
  });

  async function askThenAnswer(): Promise<string> {
    const slotId = newSlot();
    respondWith([
      frame('CUSTOM', { name: 'ask_user_question', value: { toolData: fiscalYear } }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation(''),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              asked('t1'),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(slotId, Q, request());
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: {
        ...slot(slotId).pendingAskUserQuestion!,
        answers: { 'q-fy': { questionUuid: 'q-fy', selectedOptionIds: ['latest'], userInputs: {} } },
        status: 'submitted',
      },
    });
    return slotId;
  }

  it('leaves the answered card closed when the resume asks the same question again', async () => {
    const slotId = await askThenAnswer();
    respondWith([
      frame('CUSTOM', { name: 'ask_user_question', value: { toolData: fiscalYear } }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation(''),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              asked('t1'),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
              storedMessage({ _id: 'sel', messageType: 'user_query', content: picked }),
              asked('t2'),
              storedMessage({ _id: 'a2', messageType: 'bot_response', content: '' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(slotId, picked, request({ query: picked }), { resumeAskUserQuestion: true });

    const pending = slot(slotId).pendingAskUserQuestion;
    expect(pending?.status).toBe('submitted');
    expect(pending?.payload.questions).toHaveLength(1);
    expect(slot(slotId).messages.map((m) => m.role)).toEqual(['user', 'assistant']);
  });

  it('closes the card once the agent answers instead', async () => {
    const slotId = await askThenAnswer();
    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Here is the comparison.' }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation('Here is the comparison.'),
            messages: [
              storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
              asked('t1'),
              storedMessage({ _id: 'a1', messageType: 'bot_response', content: '' }),
              storedMessage({ _id: 'sel', messageType: 'user_query', content: picked }),
              storedMessage({ _id: 'a2', messageType: 'bot_response', content: 'Here is the comparison.' }),
            ],
          },
        },
      }),
    ]);
    await streamMessageForSlot(slotId, picked, request({ query: picked }), { resumeAskUserQuestion: true });

    expect(slot(slotId).pendingAskUserQuestion?.status).toBe('submitted');
  });
});

describe('answering an approval card', () => {
  const WAITING = 'Waiting for your approval to run create_issue on Jira.';
  const CARD = { approvalId: 'ap-1', serverName: 'Jira', toolName: 'create_issue', expiresAt: 1_900_000_000_000 };
  const parked = (): ConversationMessage[] => [
    storedMessage({ _id: 'q1', messageType: 'user_query', content: Q }),
    storedMessage({
      _id: 'a1', messageType: 'bot_response', content: WAITING,
      parts: [{ type: 'tool_call', toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'awaiting_approval', approval: CARD }],
    } as Partial<ConversationMessage>),
  ];

  async function stoppedOnTheCard(): Promise<string> {
    const slotId = newSlot();
    respondWith([frame('RUN_FINISHED', { result: { conversation: { ...finishedConversation(WAITING), messages: parked() } } })]);
    await streamMessageForSlot(slotId, Q, request());
    return slotId;
  }

  const answer = (decision: 'allow' | 'deny') =>
    request({ query: `Tool approval: ${decision}`, toolApproval: { approvalId: 'ap-1', decision } });

  it('carries on the same reply: no new bubble, and the call is settled where the card was', async () => {
    const slotId = await stoppedOnTheCard();
    let liveParts: MessagePart[] = [];
    const unsubscribe = useChatStore.subscribe((state) => {
      const parts = state.slots[slotId]?.streamingParts;
      if (parts?.some((p) => p.toolCallId === 'c1' && p.approvalAnswer === 'allowed')) liveParts = parts;
    });
    respondWith([
      frame('TOOL_CALL_START', { toolCallId: 'c1', toolCallName: 'mcp_jira_create_issue', approved: true, approvalAnswer: 'allowed' }),
      frame('TOOL_CALL_END', { toolCallId: 'c1' }),
      frame('TOOL_CALL_RESULT', { toolCallId: 'c1', content: 'created JIRA-7', status: 'completed' }),
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Filed JIRA-7.' }),
      frame('RUN_FINISHED', {
        result: {
          conversation: {
            ...finishedConversation('Filed JIRA-7.'),
            messages: [
              ...parked(),
              storedMessage({ _id: 'ans', messageType: 'user_query', content: 'Tool approval: allow' }),
              storedMessage({
                _id: 'a2', messageType: 'bot_response', content: 'Filed JIRA-7.',
                parts: [{ type: 'tool_call', toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'completed', approved: true, approvalAnswer: 'allowed' }],
              } as Partial<ConversationMessage>),
            ],
          },
        },
      }),
    ]);

    const streaming = streamMessageForSlot(slotId, 'Tool approval: allow', answer('allow'), { reuseRowId: 'a1' });
    expect(texts(slotId)).toEqual([['user', Q], ['assistant', WAITING]]);
    expect(slot(slotId).streamingQuestion).toBe(Q);
    await streaming;
    unsubscribe();

    // The card's call is replaced where it was, not added again below it.
    expect(liveParts.filter((p) => p.toolCallId === 'c1')).toEqual([
      expect.objectContaining({ approved: true, approvalAnswer: 'allowed', approval: CARD }),
    ]);
    expect(texts(slotId)).toEqual([['user', Q], ['assistant', 'Filed JIRA-7.']]);
    expect(slot(slotId).messages[1].id).toBe('a1');
    expect(JSON.parse(String(fetchMock.mock.calls.at(-1)?.[1]?.body)).toolApproval).toEqual({ approvalId: 'ap-1', decision: 'allow' });
  });

  it('leaves the card as it was and says so when the answer fails to send', async () => {
    const slotId = await stoppedOnTheCard();
    const before = slot(slotId).messages;
    respondWith([frame('RUN_ERROR', { message: 'offline' })]);

    await streamMessageForSlot(slotId, 'Tool approval: deny', answer('deny'), { reuseRowId: 'a1' });

    expect(slot(slotId).messages).toEqual(before);
    expect(slot(slotId).isStreaming).toBe(false);
    expect(
      useToastStore.getState().toasts.map((t) => ({ variant: t.variant, title: t.title, description: t.description })),
    ).toEqual([{ variant: 'error', title: "Couldn't send your answer. Try again.", description: 'offline' }]);
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

describe('answering, skipping and ignoring a card in a live chat', () => {
  const question = (uuid: string, text: string, labels: string[], multiSelect = false) => ({
    uuid,
    question: text,
    multiSelect,
    options: labels.map((label, i) => ({ id: `${uuid}-o${i}`, label, isUserInput: false })),
  });
  const region = question('q-region', 'Which region?', ['EU', 'US']);
  const format = question('q-format', 'Which format?', ['Table', 'Chart']);
  const card = (...questions: ReturnType<typeof question>[]) => ({ name: 'ask_user_question', questions });
  const saved = (id: string, payload: ReturnType<typeof card>) => storedMessage({
    _id: id,
    messageType: 'tool_call',
    tools: [{ toolName: 'ask_user_question', toolResult: payload }],
  });
  const said = (id: string, content: string) => storedMessage({ _id: id, messageType: 'user_query', content });
  const replied = (id: string, content = '') => storedMessage({ _id: id, messageType: 'bot_response', content });
  const finished = (answer: string, messages: ConversationMessage[]) =>
    frame('RUN_FINISHED', { result: { conversation: { ...finishedConversation(answer), messages } } });
  const selections = (...lines: string[]) =>
    `User selections:\n${lines.map((l, i) => `${i + 1}. ${l}`).join('\n')}`;
  const pick = (uuid: string, ids: string[]) => ({ [uuid]: { questionUuid: uuid, selectedOptionIds: ids, userInputs: {} } });

  async function ask(payload: ReturnType<typeof card>, goal = Q): Promise<string> {
    const slotId = newSlot();
    respondWith([
      frame('CUSTOM', { name: 'ask_user_question', value: { toolData: payload } }),
      finished('', [said('u1', goal), saved('t1', payload), replied('b1')]),
    ]);
    await streamMessageForSlot(slotId, goal, request({ query: goal }));
    return slotId;
  }

  function submit(slotId: string, answers: Record<string, unknown>) {
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: {
        ...slot(slotId).pendingAskUserQuestion!,
        answers: answers as never,
        status: 'submitted',
      },
    });
  }

  async function resume(slotId: string, query: string, reply: string, history: ConversationMessage[]) {
    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: reply }),
      finished(reply, history),
    ]);
    await streamMessageForSlot(slotId, query, request({ query }), { resumeAskUserQuestion: true });
  }

  it('a card with two questions answered together ends closed with the reply on its row', async () => {
    const payload = card(region, format);
    const slotId = await ask(payload);
    const answers = { ...pick('q-region', ['q-region-o1']), ...pick('q-format', ['q-format-o1']) };
    submit(slotId, answers);
    const query = selections('"Which region?" → US', '"Which format?" → Chart');

    await resume(slotId, query, 'Here is the chart.', [
      said('u1', Q), saved('t1', payload), replied('b1'), said('sel', query), replied('b2', 'Here is the chart.'),
    ]);

    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({ status: 'submitted', answers });
    expect(texts(slotId)).toEqual([['user', Q], ['assistant', 'Here is the chart.']]);
  });

  it('skipping a card closes it and the reply still lands on its row', async () => {
    const payload = card(region);
    const slotId = await ask(payload);
    const answers = pick('q-region', ['__no_preference__']);
    submit(slotId, answers);
    const query = selections('"Which region?" → [No preference]');

    await resume(slotId, query, 'Going global.', [
      said('u1', Q), saved('t1', payload), replied('b1'), said('sel', query), replied('b2', 'Going global.'),
    ]);

    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({ status: 'submitted', answers });
    expect(texts(slotId)).toEqual([['user', Q], ['assistant', 'Going global.']]);
  });

  it('skipping one of two questions keeps the other answer on the closed card', async () => {
    const payload = card(region, format);
    const slotId = await ask(payload);
    const answers = { ...pick('q-region', ['__no_preference__']), ...pick('q-format', ['q-format-o0']) };
    submit(slotId, answers);
    const query = selections('"Which region?" → [No preference]', '"Which format?" → Table');

    await resume(slotId, query, 'Table, any region.', [
      said('u1', Q), saved('t1', payload), replied('b1'), said('sel', query), replied('b2', 'Table, any region.'),
    ]);

    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({ status: 'submitted', answers });
  });

  it('chatting on without answering leaves the card pending on its own row', async () => {
    const payload = card(region);
    const slotId = await ask(payload, 'plan the launch');

    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Hi!' }),
      finished('Hi!', [
        said('u1', 'plan the launch'), saved('t1', payload), replied('b1'), said('u2', 'hello'), replied('b2', 'Hi!'),
      ]),
    ]);
    await streamMessageForSlot(slotId, 'hello', request({ query: 'hello' }));

    expect(texts(slotId)).toEqual([['user', 'plan the launch'], ['assistant', ''], ['user', 'hello'], ['assistant', 'Hi!']]);
    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({
      status: 'pending',
      assistantMessageId: slot(slotId).messages[1].id,
    });
  });

  it('stopping as the card appears, then chatting, then answering puts the reply under the card', async () => {
    fakeApi({ 'POST /api/v1/conversations/conv-real/cancel': { status: 200, data: { cancelled: true } } });
    const payload = card(region);
    const slotId = newSlot('conv-real');
    let close!: (...lastFrames: string[]) => void;
    const encoder = new TextEncoder();
    fetchMock.mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frame('CUSTOM', {
          name: 'ask_user_question',
          value: { toolData: payload },
        })));
        close = (...lastFrames) => {
          for (const f of lastFrames) controller.enqueue(encoder.encode(f));
          controller.close();
        };
      },
    }), { status: 200 }));

    const run = streamMessageForSlot(
      slotId,
      'helpme',
      request({ query: 'helpme', conversationId: 'conv-real' }),
    );
    await vi.waitFor(() => expect(slot(slotId).pendingAskUserQuestion?.status).toBe('pending'));
    const cardRowId = slot(slotId).pendingAskUserQuestion!.assistantMessageId;

    vi.useFakeTimers();
    cancelStreamForSlot(slotId);
    close();
    await run;
    await vi.advanceTimersByTimeAsync(5_000);
    vi.useRealTimers();

    expect(slot(slotId).isStreaming).toBe(false);
    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({
      status: 'pending',
      assistantMessageId: cardRowId,
    });

    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Hi!' }),
      finished('Hi!', [
        said('u1', 'helpme'), saved('t1', payload),
        storedMessage({ _id: 'b1', messageType: 'bot_response', content: '', status: 'stopped' }),
        said('u2', 'hiii'), replied('b2', 'Hi!'),
      ]),
    ]);
    await streamMessageForSlot(slotId, 'hiii', request({ query: 'hiii', conversationId: 'conv-real' }));

    const answers = pick('q-region', ['q-region-o0']);
    submit(slotId, answers);
    const query = selections('"Which region?" → EU');
    await resume(slotId, query, 'Planning the EU launch.', [
      said('u1', 'helpme'), saved('t1', payload),
      storedMessage({ _id: 'b1', messageType: 'bot_response', content: '', status: 'stopped' }),
      said('u2', 'hiii'), replied('b2', 'Hi!'),
      said('sel', query), replied('b3', 'Planning the EU launch.'),
    ]);

    expect(texts(slotId)).toEqual([
      ['user', 'helpme'], ['assistant', 'Planning the EU launch.'],
      ['user', 'hiii'], ['assistant', 'Hi!'],
    ]);
    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({
      status: 'submitted',
      assistantMessageId: slot(slotId).messages[1].id,
      answers,
    });
    expect(slot(slotId).messages.some((m) => m.metadata?.custom?.status === 'stopped')).toBe(false);
  });

  it('a later chat does not write its reply onto the unanswered card', async () => {
    const payload = card(region);
    const slotId = await ask(payload, 'ask me one question');
    const later = 'You asked me to: send an email saying Hello from Pipeshub.';
    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: later }),
      finished(later, [
        said('u1', 'ask me one question'), saved('t1', payload), replied('b1'),
        said('u2', 'what are the things i asked u to do'), replied('b2', later),
      ]),
    ]);
    await streamMessageForSlot(
      slotId,
      'what are the things i asked u to do',
      request({ query: 'what are the things i asked u to do' }),
    );

    const cardRow = slot(slotId).messages[1];
    expect(getThreadMessagePlainText(cardRow)).toBe('');
    expect(texts(slotId)).toEqual([
      ['user', 'ask me one question'], ['assistant', ''],
      ['user', 'what are the things i asked u to do'], ['assistant', later],
    ]);
    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({
      status: 'pending',
      assistantMessageId: cardRow.id,
    });
  });

  it('answering the card after chatting on puts the reply under the card', async () => {
    const payload = card(region);
    const slotId = await ask(payload, 'plan the launch');
    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Hi!' }),
      finished('Hi!', [
        said('u1', 'plan the launch'), saved('t1', payload), replied('b1'), said('u2', 'hello'), replied('b2', 'Hi!'),
      ]),
    ]);
    await streamMessageForSlot(slotId, 'hello', request({ query: 'hello' }));
    const cardRowId = slot(slotId).messages[1].id;

    const answers = pick('q-region', ['q-region-o0']);
    submit(slotId, answers);
    const query = selections('"Which region?" → EU');
    await resume(slotId, query, 'Planning for EU.', [
      said('u1', 'plan the launch'), saved('t1', payload), replied('b1'), said('u2', 'hello'), replied('b2', 'Hi!'),
      said('sel', query), replied('b3', 'Planning for EU.'),
    ]);

    expect(texts(slotId)).toEqual([
      ['user', 'plan the launch'], ['assistant', 'Planning for EU.'],
      ['user', 'hello'], ['assistant', 'Hi!'],
    ]);
    expect(slot(slotId).messages[1].id).toBe(cardRowId);
    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({ status: 'submitted', assistantMessageId: cardRowId, answers });
    expect(getThreadMessagePlainText(slot(slotId).messages[1])).not.toBe('Hi!');
  });

  it('does not copy a later chat onto the card when saved history put User selections first', async () => {
    const payload = card(region);
    const slotId = await ask(payload, 'ask me one question');
    const later = 'You asked me to: send an email saying Hello from Pipeshub.';
    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: later }),
      finished(later, [
        said('u1', 'ask me one question'), saved('t1', payload), replied('b1'),
        said('u2', 'what are the things i asked u to do'), replied('b2', later),
      ]),
    ]);
    await streamMessageForSlot(
      slotId,
      'what are the things i asked u to do',
      request({ query: 'what are the things i asked u to do' }),
    );
    const cardRowId = slot(slotId).messages[1].id;
    const answers = pick('q-region', ['q-region-o0']);
    submit(slotId, answers);
    const query = selections('"Which region?" → EU');
    const reply = 'Nice, work or study it is.';
    await resume(slotId, query, reply, [
      said('u1', 'ask me one question'), saved('t1', payload), replied('b1'),
      said('sel', query),
      said('u2', 'what are the things i asked u to do'), replied('b2', later),
    ]);

    expect(getThreadMessagePlainText(slot(slotId).messages[1])).toBe(reply);
    expect(getThreadMessagePlainText(slot(slotId).messages[1])).not.toContain('You asked me to');
    expect(slot(slotId).messages[1].id).toBe(cardRowId);
    expect(texts(slotId)).toEqual([
      ['user', 'ask me one question'], ['assistant', reply],
      ['user', 'what are the things i asked u to do'], ['assistant', later],
    ]);
  });

  it('while the answer streams in after a later chat, it stays on the card row', async () => {
    const payload = card(region);
    const slotId = await ask(payload, 'plan the launch');
    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Hi!' }),
      finished('Hi!', [
        said('u1', 'plan the launch'), saved('t1', payload), replied('b1'), said('u2', 'hello'), replied('b2', 'Hi!'),
      ]),
    ]);
    await streamMessageForSlot(slotId, 'hello', request({ query: 'hello' }));
    const cardRowId = slot(slotId).messages[1].id;
    submit(slotId, pick('q-region', ['q-region-o0']));

    respondWith({
      chunks: [frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'Planning for EU' })],
      hang: true,
    });
    const query = selections('"Which region?" → EU');
    const run = streamMessageForSlot(slotId, query, request({ query }), { resumeAskUserQuestion: true });
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toBe('Planning for EU'));

    expect(slot(slotId).streamingQuestion).toBe('plan the launch');
    expect(slot(slotId).messages.map((m) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant']);
    expect(slot(slotId).messages[1].id).toBe(cardRowId);
    expect(slot(slotId).messages.some((m) => getThreadMessagePlainText(m).startsWith('User selections:'))).toBe(false);

    slot(slotId).abortController?.abort();
    await run;
  });
  it('marks the answers that went with a submit as sent when the agent asks more', async () => {
    const slotId = await ask(card(region));
    const sent = pick('q-region', ['q-region-o0']);
    submit(slotId, sent);
    respondWith({
      chunks: [frame('CUSTOM', { name: 'ask_user_question', value: { toolData: card(format) } })],
      hang: true,
    });
    const query = selections('"Which region?" → EU');
    const run = streamMessageForSlot(slotId, query, request({ query }), { resumeAskUserQuestion: true });
    await vi.waitFor(() => expect(slot(slotId).pendingAskUserQuestion?.status).toBe('pending'));

    expect(slot(slotId).pendingAskUserQuestion?.payload.questions).toHaveLength(2);
    expect(slot(slotId).pendingAskUserQuestion?.sentAnswers).toEqual(sent);

    slot(slotId).abortController?.abort();
    await run;
  });

  it('does not mark answers as sent for a card that was never submitted', async () => {
    const slotId = await ask(card(region));

    expect(slot(slotId).pendingAskUserQuestion?.sentAnswers).toBeUndefined();
  });

  it('a second card in a later turn does not disturb the first answered card', async () => {
    const first = card(region);
    const slotId = await ask(first, 'plan the launch');
    submit(slotId, pick('q-region', ['q-region-o0']));
    const firstQuery = selections('"Which region?" → EU');
    await resume(slotId, firstQuery, 'Planning for EU.', [
      said('u1', 'plan the launch'), saved('t1', first), replied('b1'), said('s1', firstQuery), replied('b2', 'Planning for EU.'),
    ]);

    const second = card(format);
    respondWith([
      frame('CUSTOM', { name: 'ask_user_question', value: { toolData: second } }),
      finished('', [
        said('u1', 'plan the launch'), saved('t1', first), replied('b1'), said('s1', firstQuery), replied('b2', 'Planning for EU.'),
        said('u2', 'now the report'), saved('t2', second), replied('b3'),
      ]),
    ]);
    await streamMessageForSlot(slotId, 'now the report', request({ query: 'now the report' }));

    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({ status: 'pending' });
    expect(slot(slotId).pendingAskUserQuestion?.payload.questions.map((q) => q.uuid)).toEqual(['q-format']);
    expect(texts(slotId)[1]).toEqual(['assistant', 'Planning for EU.']);
  });

  it('stopping a resume leaves the card retryable with the answers kept', async () => {
    fakeApi({ 'POST /api/v1/conversations/conv-real/cancel': { status: 200, data: { cancelled: true } } });
    const payload = card(region);
    const slotId = await ask(payload);
    const answers = pick('q-region', ['q-region-o0']);
    submit(slotId, answers);
    const query = selections('"Which region?" → EU');

    let close!: (...lastFrames: string[]) => void;
    const encoder = new TextEncoder();
    fetchMock.mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frame('TEXT_MESSAGE_START')));
        controller.enqueue(encoder.encode(frame('TEXT_MESSAGE_CONTENT', { delta: 'Working on EU' })));
        close = (...lastFrames) => {
          for (const f of lastFrames) controller.enqueue(encoder.encode(f));
          controller.close();
        };
      },
    }), { status: 200 }));

    const run = streamMessageForSlot(
      slotId,
      query,
      request({ query, conversationId: 'conv-real' }),
      { resumeAskUserQuestion: true },
    );
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toContain('Working on EU'));

    vi.useFakeTimers();
    cancelStreamForSlot(slotId);
    close();
    await run;
    await vi.advanceTimersByTimeAsync(5_000);
    vi.useRealTimers();

    expect(slot(slotId).isStreaming).toBe(false);
    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({ status: 'submitted', answers });
    expect(texts(slotId)[0]).toEqual(['user', Q]);
    expect(texts(slotId).some(([, text]) => String(text).startsWith('User selections:'))).toBe(false);
  });

  it('Stop during a late resume writes the partial onto the card, not the later chat', async () => {
    fakeApi({ 'POST /api/v1/conversations/conv-real/cancel': { status: 200, data: { cancelled: true } } });
    const payload = card(region);
    const slotId = await ask(payload, 'plan the launch');
    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Hi!' }),
      finished('Hi!', [
        said('u1', 'plan the launch'), saved('t1', payload), replied('b1'),
        said('u2', 'hello'), replied('b2', 'Hi!'),
      ]),
    ]);
    await streamMessageForSlot(slotId, 'hello', request({ query: 'hello' }));
    const cardRowId = slot(slotId).messages[1].id;
    submit(slotId, pick('q-region', ['q-region-o0']));

    respondWith({
      chunks: [frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'Working on EU' })],
      hang: true,
    });
    const query = selections('"Which region?" → EU');
    const run = streamMessageForSlot(slotId, query, request({ query }), { resumeAskUserQuestion: true });
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toBe('Working on EU'));

    vi.useFakeTimers();
    cancelStreamForSlot(slotId);
    await vi.advanceTimersByTimeAsync(5_000);
    vi.useRealTimers();
    slot(slotId).abortController?.abort();
    await run;

    expect(slot(slotId).messages[1].id).toBe(cardRowId);
    expect(getThreadMessagePlainText(slot(slotId).messages[1])).toBe('Working on EU');
    expect(getThreadMessagePlainText(slot(slotId).messages[3])).toBe('Hi!');
    expect((slot(slotId).messages[3].metadata?.custom as { status?: string } | undefined)?.status).toBeUndefined();
  });

  it('a retried answer after a stopped resume closes the card once', async () => {
    fakeApi({ 'POST /api/v1/conversations/conv-real/cancel': { status: 200, data: { cancelled: true } } });
    const payload = card(region);
    const slotId = await ask(payload);
    const answers = pick('q-region', ['q-region-o0']);
    submit(slotId, answers);
    const query = selections('"Which region?" → EU');

    let close!: (...lastFrames: string[]) => void;
    const encoder = new TextEncoder();
    fetchMock.mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frame('TEXT_MESSAGE_START')));
        controller.enqueue(encoder.encode(frame('TEXT_MESSAGE_CONTENT', { delta: 'Working on EU' })));
        close = (...lastFrames) => {
          for (const f of lastFrames) controller.enqueue(encoder.encode(f));
          controller.close();
        };
      },
    }), { status: 200 }));

    const run = streamMessageForSlot(
      slotId,
      query,
      request({ query, conversationId: 'conv-real' }),
      { resumeAskUserQuestion: true },
    );
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toContain('Working on EU'));
    vi.useFakeTimers();
    cancelStreamForSlot(slotId);
    close();
    await run;
    await vi.advanceTimersByTimeAsync(5_000);
    vi.useRealTimers();

    submit(slotId, answers);
    await resume(slotId, query, 'EU it is.', [
      said('u1', Q), saved('t1', payload), replied('b1'), said('sel', query), replied('b2', 'EU it is.'),
    ]);

    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({ status: 'submitted', answers });
    expect(texts(slotId)).toEqual([['user', Q], ['assistant', 'EU it is.']]);
  });

  it('a stream that dies after the answer was sent leaves the card retryable with the answers kept', async () => {
    const payload = card(region);
    const slotId = await ask(payload);
    const answers = pick('q-region', ['q-region-o0']);
    submit(slotId, answers);

    respondWith([frame('RUN_ERROR', { message: 'offline' })]);
    await streamMessageForSlot(slotId, selections('"Which region?" → EU'), request(), { resumeAskUserQuestion: true });

    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({ status: 'pending', answers });
    expect(texts(slotId)).toEqual([['user', Q], ['assistant', '']]);
  });

  it('a retried answer after a failed one closes the card once', async () => {
    const payload = card(region);
    const slotId = await ask(payload);
    const answers = pick('q-region', ['q-region-o0']);
    submit(slotId, answers);
    const query = selections('"Which region?" → EU');

    respondWith([frame('RUN_ERROR', { message: 'offline' })]);
    await streamMessageForSlot(slotId, query, request({ query }), { resumeAskUserQuestion: true });
    submit(slotId, answers);
    await resume(slotId, query, 'EU it is.', [
      said('u1', Q), saved('t1', payload), replied('b1'), said('sel', query), replied('b2', 'EU it is.'),
    ]);

    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({ status: 'submitted', answers });
    expect(texts(slotId)).toEqual([['user', Q], ['assistant', 'EU it is.']]);
  });

  it('a normal question after an answered card gets its own row and leaves the card alone', async () => {
    const payload = card(region);
    const slotId = await ask(payload, 'plan the launch');
    submit(slotId, pick('q-region', ['q-region-o0']));
    const query = selections('"Which region?" → EU');
    await resume(slotId, query, 'Planning for EU.', [
      said('u1', 'plan the launch'), saved('t1', payload), replied('b1'), said('sel', query), replied('b2', 'Planning for EU.'),
    ]);

    respondWith([
      frame('TEXT_MESSAGE_START'),
      frame('TEXT_MESSAGE_CONTENT', { delta: 'Budget is 10k.' }),
      finished('Budget is 10k.', [
        said('u1', 'plan the launch'), saved('t1', payload), replied('b1'), said('sel', query), replied('b2', 'Planning for EU.'),
        said('u2', 'and the budget?'), replied('b3', 'Budget is 10k.'),
      ]),
    ]);
    await streamMessageForSlot(slotId, 'and the budget?', request({ query: 'and the budget?' }));

    expect(texts(slotId)).toEqual([
      ['user', 'plan the launch'], ['assistant', 'Planning for EU.'], ['user', 'and the budget?'], ['assistant', 'Budget is 10k.'],
    ]);
    expect(slot(slotId).pendingAskUserQuestion?.status).not.toBe('pending');
  });
});

describe('a long run of questions, loaded one page at a time', () => {
  const COUNT = 17;
  const PAGE = 20;
  const pageOf = (rows: ConversationMessage[], page: number) => {
    const end = rows.length - (page - 1) * PAGE;
    return rows.slice(Math.max(0, end - PAGE), end);
  };
  const pagination = (rows: ConversationMessage[], page: number) => ({
    page,
    limit: PAGE,
    totalCount: rows.length,
    totalPages: Math.ceil(rows.length / PAGE),
    hasNextPage: page * PAGE < rows.length,
    hasPrevPage: page > 1,
  });
  const payload = (i: number) => ({
    name: 'ask_user_question',
    questions: [{
      uuid: `q${i}`,
      question: `Random question number ${i}?`,
      multiSelect: false,
      options: [
        { id: `q${i}-a`, label: i % 4 === 0 ? 'Yes, definitely' : 'Yes', isUserInput: false },
        { id: `q${i}-b`, label: 'No', isUserInput: false },
      ],
    }],
  });
  const answer = (i: number) => (i % 4 === 0 ? 'Yes, definitely' : 'Yes');

  function history(answered: number, finalReply = ''): ConversationMessage[] {
    const rows = [storedMessage({ _id: 'u0', messageType: 'user_query', content: 'ask me random questions' })];
    for (let i = 1; i <= COUNT; i += 1) {
      rows.push(
        storedMessage({ _id: `t${i}`, messageType: 'tool_call', tools: [{ toolName: 'ask_user_question', toolResult: payload(i) }] }),
        storedMessage({ _id: `b${i}`, messageType: 'bot_response', content: '' }),
      );
      if (i > answered) break;
      rows.push(storedMessage({
        _id: `s${i}`,
        messageType: 'user_query',
        content: `User selections:\n1. "Random question number ${i}?" → ${answer(i)}`,
      }));
    }
    if (finalReply) rows.push(storedMessage({ _id: 'b-final', messageType: 'bot_response', content: finalReply }));
    return rows;
  }

  function openAtFirstPage(rows: ConversationMessage[]): string {
    const slotId = newSlot('conv-1');
    const first = pageOf(rows, 1);
    const built = loadHistoricalMessages(first);
    useChatStore.getState().updateSlot(slotId, {
      messages: built.messages,
      rawMessages: first,
      pendingAskUserQuestion: built.unansweredAskUserQuestion,
      isInitialized: true,
      hasLoaded: true,
      messagePagination: { currentPage: 1, hasOlderMessages: pagination(rows, 1).hasNextPage, isLoadingOlder: false },
    });
    return slotId;
  }

  function serve(rows: ConversationMessage[], ...pages: number[]) {
    const get = vi.spyOn(apiClient, 'get');
    for (const page of pages) {
      get.mockResolvedValueOnce({
        data: { conversation: { messages: pageOf(rows, page), pagination: pagination(rows, page) } },
      });
    }
  }

  const cardRows = (id: string) =>
    slot(id).messages.filter((m) => (m.metadata?.custom as Record<string, unknown> | undefined)?.persistedAskUserQuestion);
  const cardQuestions = (id: string) => {
    const [row] = cardRows(id);
    const card = (row.metadata?.custom as { persistedAskUserQuestion: { questions: Array<{ question: string }> } }).persistedAskUserQuestion;
    return card.questions.map((q) => Number(q.question.replace(/\D/g, '')));
  };
  const all = Array.from({ length: COUNT }, (_, i) => i + 1);

  it('first shows only the questions of the newest page', () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);

    expect(cardQuestions(slotId)).toEqual([11, 12, 13, 14, 15, 16, 17]);
  });

  it('gives back every question as one card once all pages are loaded', async () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);
    serve(rows, 2, 3);

    await loadOlderMessagesForSlot(slotId);
    await loadOlderMessagesForSlot(slotId);

    expect(cardRows(slotId)).toHaveLength(1);
    expect(cardQuestions(slotId)).toEqual(all);
    expect(slot(slotId).messagePagination).toEqual({ currentPage: 3, hasOlderMessages: false, isLoadingOlder: false });
  });

  it('shows what a full reload shows, row for row', async () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);
    serve(rows, 2, 3);

    await loadOlderMessagesForSlot(slotId);
    await loadOlderMessagesForSlot(slotId);

    expect(slot(slotId).messages).toEqual(loadHistoricalMessages(rows).messages);
    expect(slot(slotId).pendingAskUserQuestion).toEqual(loadHistoricalMessages(rows).unansweredAskUserQuestion);
  });

  it('keeps the answers of the older questions after each page', async () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);
    serve(rows, 2, 3);

    await loadOlderMessagesForSlot(slotId);
    const afterSecond = Object.keys(slot(slotId).pendingAskUserQuestion?.answers ?? {});
    await loadOlderMessagesForSlot(slotId);
    const afterThird = Object.keys(slot(slotId).pendingAskUserQuestion?.answers ?? {});

    expect(afterSecond.length).toBeGreaterThan(6);
    expect(afterThird).toEqual(all.slice(0, -1).map((i) => `q${i}`));
  });

  it('keeps the last question open and bound to a card row after every page', async () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);
    serve(rows, 2, 3);

    for (let page = 2; page <= 3; page += 1) {
      await loadOlderMessagesForSlot(slotId);
      const pending = slot(slotId).pendingAskUserQuestion;
      expect(pending?.status).toBe('pending');
      expect(cardRows(slotId).map((m) => m.id)).toContain(pending?.assistantMessageId);
    }
    expect(cardRows(slotId).map((m) => m.id)).toEqual([slot(slotId).pendingAskUserQuestion?.assistantMessageId]);
  });

  it('reads an answer with a comma correctly on an older page', async () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);
    serve(rows, 2, 3);

    await loadOlderMessagesForSlot(slotId);
    await loadOlderMessagesForSlot(slotId);

    const answers = slot(slotId).pendingAskUserQuestion?.answers ?? {};
    expect(answers.q4.selectedOptionIds).toEqual(['q4-a']);
    expect(answers.q8.selectedOptionIds).toEqual(['q8-a']);
    expect(Object.values(answers).every((a) => a.selectedOptionIds.length === 1)).toBe(true);
  });

  it('keeps an answer the user picked but has not sent yet', async () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: {
        ...slot(slotId).pendingAskUserQuestion!,
        answers: {
          ...slot(slotId).pendingAskUserQuestion!.answers,
          q17: { questionUuid: 'q17', selectedOptionIds: ['q17-b'], userInputs: {} },
        },
      },
    });
    serve(rows, 2, 3);

    await loadOlderMessagesForSlot(slotId);
    await loadOlderMessagesForSlot(slotId);

    expect(slot(slotId).pendingAskUserQuestion?.answers.q17.selectedOptionIds).toEqual(['q17-b']);
  });

  it('closes the whole card with every answer when the run ended in a reply', async () => {
    const rows = history(COUNT, 'That was fun.');
    const slotId = openAtFirstPage(rows);
    expect(slot(slotId).pendingAskUserQuestion).toBeNull();
    serve(rows, 2, 3);

    await loadOlderMessagesForSlot(slotId);
    await loadOlderMessagesForSlot(slotId);

    expect(slot(slotId).pendingAskUserQuestion).toBeNull();
    expect(cardRows(slotId)).toHaveLength(1);
    const custom = cardRows(slotId)[0].metadata?.custom as { persistedAskUserQuestionAnswers: Record<string, unknown> };
    expect(Object.keys(custom.persistedAskUserQuestionAnswers)).toHaveLength(COUNT);
    expect(texts(slotId)).toEqual([['user', 'ask me random questions'], ['assistant', 'That was fun.']]);
  });

  it('puts the user message and the card in order once the first page of the chat arrives', async () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);
    serve(rows, 2, 3);

    await loadOlderMessagesForSlot(slotId);
    expect(slot(slotId).messages[0].role).toBe('assistant');
    await loadOlderMessagesForSlot(slotId);

    expect(slot(slotId).messages.map((m) => m.role)).toEqual(['user', 'assistant']);
  });

  it('does not rebuild a thread that has live rows in it, and still prepends the older page', async () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);
    const live = { id: 'live-row', role: 'assistant' as const, content: [{ type: 'text' as const, text: 'streaming' }] };
    useChatStore.getState().updateSlot(slotId, { messages: [...slot(slotId).messages, live] });
    serve(rows, 2);

    await loadOlderMessagesForSlot(slotId);

    const ids = slot(slotId).messages.map((m) => m.id);
    expect(ids[ids.length - 1]).toBe('live-row');
    expect(ids.length).toBeGreaterThan(2);
  });

  it('does not rebuild while an answer is streaming', async () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);
    useChatStore.getState().updateSlot(slotId, { isStreaming: true });
    serve(rows, 2);

    await loadOlderMessagesForSlot(slotId);

    expect(slot(slotId).rawMessages).toEqual(pageOf(rows, 1));
    expect(slot(slotId).messages.length).toBeGreaterThan(0);
  });

  it('a page that adds nothing new ends the paging', async () => {
    const rows = history(COUNT - 1);
    const slotId = openAtFirstPage(rows);
    serve(rows, 1);

    await loadOlderMessagesForSlot(slotId);

    expect(slot(slotId).messagePagination).toEqual({ currentPage: 2, hasOlderMessages: false, isLoadingOlder: false });
  });
});

describe('an answer that finishes while another chat is open', () => {
  const toasts = () => useToastStore.getState().toasts;
  const unread = () => [...useChatStore.getState().unreadConversationIds];

  /** Open `other` as the visible chat, so `slotId` runs in the background. */
  function viewAnotherChat(): string {
    const other = useChatStore.getState().createSlot('conv-other');
    useChatStore.setState({ activeSlotId: other });
    return other;
  }

  beforeEach(() => {
    useChatStore.setState({ unreadConversationIds: new Set() });
  });

  it('tells the user a new chat is ready and marks it unread', async () => {
    const slotId = newSlot();
    viewAnotherChat();
    respondWith([
      frame('CUSTOM', { name: 'conversation_created', value: { conversationId: 'conv-real', title: 'Quarterly revenue' } }),
      frame('RUN_FINISHED', { result: { conversation: finishedConversation('Revenue was $4.2M.') } }),
    ]);

    await streamMessageForSlot(slotId, Q, request());

    expect(toasts().map((t) => ({ variant: t.variant, title: t.title, description: t.description, href: t.action?.href }))).toEqual([
      { variant: 'info', title: 'Answer ready', description: 'Quarterly revenue', href: '/chat/?conversationId=conv-real' },
    ]);
    expect(unread()).toEqual(['conv-real']);
  });

  it('does the same for a new message in an existing chat', async () => {
    const slotId = newSlot('conv-old');
    viewAnotherChat();
    useChatStore.setState({ conversations: [{ id: 'conv-old', title: 'Old chat' } as never] });
    respondWith([frame('RUN_FINISHED', { result: { conversation: { ...finishedConversation('ok'), _id: 'conv-old' } } })]);

    await streamMessageForSlot(slotId, Q, request({ conversationId: 'conv-old' }));

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].description).toBe('Old chat');
    expect(unread()).toEqual(['conv-old']);
  });

  it('does the same when a regenerated answer is saved', async () => {
    const slotId = newSlot('conv-1');
    viewAnotherChat();
    useChatStore.getState().updateSlot(slotId, {
      messages: [
        { id: 'q1', role: 'user', content: [{ type: 'text', text: 'Q' }] },
        { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'old answer' }] },
      ],
    });
    respondWith([frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'new' }), frame('RUN_FINISHED', { result: {} })]);
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: { conversation: { ...finishedConversation('new answer'), id: 'conv-1' } },
    });

    await streamRegenerateForSlot(slotId, 'a1', { modelKey: 'm1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' });

    expect(toasts().map((t) => t.title)).toEqual(['Answer ready']);
    expect(unread()).toEqual(['conv-1']);
  });

  it('says nothing when the user is watching that chat', async () => {
    const slotId = newSlot('conv-old');
    respondWith([frame('RUN_FINISHED', { result: { conversation: { ...finishedConversation('ok'), _id: 'conv-old' } } })]);

    await streamMessageForSlot(slotId, Q, request({ conversationId: 'conv-old' }));

    expect(toasts()).toEqual([]);
    expect(unread()).toEqual([]);
  });

  it('says nothing about a regenerated answer the user is watching', async () => {
    const slotId = newSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, {
      messages: [
        { id: 'q1', role: 'user', content: [{ type: 'text', text: 'Q' }] },
        { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'old answer' }] },
      ],
    });
    respondWith([frame('TEXT_MESSAGE_START'), frame('TEXT_MESSAGE_CONTENT', { delta: 'new' }), frame('RUN_FINISHED', { result: {} })]);
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      data: { conversation: { ...finishedConversation('new answer'), id: 'conv-1' } },
    });

    await streamRegenerateForSlot(slotId, 'a1', { modelKey: 'm1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' });

    expect(toasts()).toEqual([]);
    expect(unread()).toEqual([]);
  });

  it('still tells the user when they have left the chat area', async () => {
    const slotId = newSlot('conv-old');
    window.history.replaceState(null, '', '/knowledge-base/');
    respondWith([frame('RUN_FINISHED', { result: { conversation: { ...finishedConversation('ok'), _id: 'conv-old' } } })]);

    await streamMessageForSlot(slotId, Q, request({ conversationId: 'conv-old' }));

    expect(toasts().map((t) => t.title)).toEqual(['Answer ready']);
    expect(unread()).toEqual(['conv-old']);
  });

  it('says nothing when the answer fails', async () => {
    const slotId = newSlot('conv-old');
    viewAnotherChat();
    respondWith([frame('RUN_ERROR', { message: 'The AI model is not responding right now.', code: 'stream_error' })]);

    await streamMessageForSlot(slotId, Q, request({ conversationId: 'conv-old' }));

    expect(toasts()).toEqual([]);
    expect(unread()).toEqual([]);
  });

  it('says nothing when a regenerate request fails', async () => {
    const slotId = newSlot('conv-1');
    viewAnotherChat();
    useChatStore.getState().updateSlot(slotId, {
      messages: [
        { id: 'q1', role: 'user', content: [{ type: 'text', text: 'Q' }] },
        { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'old answer' }] },
      ],
    });
    respondWith(jsonResponse(500, {}));

    await streamRegenerateForSlot(slotId, 'a1', { modelKey: 'm1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' });

    expect(toasts()).toEqual([]);
    expect(unread()).toEqual([]);
  });

  it('says nothing when the user stopped the run, even if the server still finishes it', async () => {
    fakeApi({ 'POST /api/v1/conversations/conv-1/cancel': { status: 200, data: { cancelled: true } } });
    const slotId = newSlot('conv-1');
    viewAnotherChat();
    const encoder = new TextEncoder();
    let close!: (...lastFrames: string[]) => void;
    fetchMock.mockResolvedValueOnce(
      new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode(frame('TEXT_MESSAGE_START')));
            controller.enqueue(encoder.encode(frame('TEXT_MESSAGE_CONTENT', { delta: 'Revenue was' })));
            close = (...lastFrames) => {
              for (const f of lastFrames) controller.enqueue(encoder.encode(f));
              controller.close();
            };
          },
        }),
        { status: 200 },
      ),
    );
    const run = streamMessageForSlot(slotId, Q, request({ conversationId: 'conv-1' }));
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toBe('Revenue was'));

    vi.useFakeTimers();
    cancelStreamForSlot(slotId);
    close(frame('RUN_FINISHED', { result: { conversation: { ...finishedConversation('Revenue was'), _id: 'conv-1' } } }));
    await run;
    await vi.advanceTimersByTimeAsync(5_000);
    vi.useRealTimers();

    expect(slot(slotId).isStreaming).toBe(false);
    expect(toasts()).toEqual([]);
    expect(unread()).toEqual([]);
  });
});