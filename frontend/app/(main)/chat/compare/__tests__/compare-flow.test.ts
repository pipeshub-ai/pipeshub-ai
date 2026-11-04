/**
 * Compare models end to end below the UI: the composer's send, the primary
 * stream's `turn_started`, the hidden branch conversation streaming in its own
 * slot, choosing, and cleanup. Only `fetch` and the axios adapter are faked.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  installMemoryStorage,
  jwtExpiringIn,
  sseFrame,
  sseResponse,
  type StreamPlan,
} from '@/lib/api/__tests__/sse-response';
import type { AvailableLlmModel, ConversationMessage } from '../../types';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { useToastStore } = await import('@/lib/store/toast-store');
const { ASSISTANT_CTX, useChatStore } = await import('../../store');
const { buildExternalStoreConfig } = await import('../../runtime');
const { useCompareStore } = await import('../compare-store');
const controller = await import('../compare-controller');
const { columnStatus } = await import('../column-state');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');

const fetchMock = vi.fn<typeof fetch>();
const initialChat = useChatStore.getState();

const MODEL_A = { modelKey: 'ka', modelName: 'model-a', modelFriendlyName: 'Model A', modelProvider: 'openAI' };
const MODEL_B = { modelKey: 'kb', modelName: 'model-b', modelFriendlyName: 'Model B', modelProvider: 'anthropic' };
const DEFAULT = { modelKey: 'kd', modelName: 'model-d', modelFriendlyName: 'Model D' };
const llm = (m: typeof MODEL_A): AvailableLlmModel =>
  ({ modelType: 'llm', provider: m.modelProvider, modelKey: m.modelKey, modelName: m.modelName, modelFriendlyName: m.modelFriendlyName }) as AvailableLlmModel;

const frame = (type: string, fields: Record<string, unknown> = {}) => sseFrame(type, { type, ...fields });
const turnStarted = () =>
  frame('CUSTOM', { name: 'turn_started', value: { conversationId: 'conv-1', userMessageId: 'u1' } });

const row = (over: Partial<ConversationMessage>): ConversationMessage =>
  ({
    _id: 'm',
    messageType: 'user_query',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: '2026-09-18T00:00:00.000Z',
    updatedAt: '2026-09-18T00:00:00.000Z',
    ...over,
  }) as ConversationMessage;

function conversation(id: string, answer: string, model: typeof MODEL_A) {
  return {
    _id: id,
    title: 'Compare',
    createdAt: '2026-09-18T00:00:00.000Z',
    updatedAt: '2026-09-18T00:00:00.000Z',
    isShared: false,
    status: 'complete',
    modelInfo: { modelKey: model.modelKey, modelName: model.modelName, chatMode: 'quick' },
    messages: [
      row({ _id: 'u1', messageType: 'user_query', content: 'What was Q3 revenue?' }),
      row({
        _id: `bot-${id}`,
        messageType: 'bot_response',
        content: answer,
        modelInfo: { modelKey: model.modelKey, modelName: model.modelName, modelFriendlyName: model.modelFriendlyName, chatMode: 'quick' },
      }),
    ],
  };
}
const finish = (id: string, answer: string, model: typeof MODEL_A) => [
  frame('TEXT_MESSAGE_START'),
  frame('TEXT_MESSAGE_CONTENT', { delta: answer }),
  frame('TEXT_MESSAGE_END'),
  frame('RUN_FINISHED', { result: { conversation: conversation(id, answer, model) } }),
];

interface Sent {
  path: string;
  body: Record<string, unknown>;
  signal?: AbortSignal | null;
}
let streams: Record<string, (init?: RequestInit) => Promise<Response>>;
let sentStreams: Sent[];
let events: string[];
let api: ReturnType<typeof fakeApi>;

const planOf = (chunks: string[], extra: Partial<StreamPlan> = {}) => async (init?: RequestInit) =>
  sseResponse({ chunks, signal: init?.signal, ...extra });
function gated(chunks: string[]) {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  return {
    release,
    handler: async (init?: RequestInit) => {
      await gate;
      return sseResponse({ chunks, signal: init?.signal });
    },
  };
}

const PRIMARY = '/api/v1/conversations/conv-1/messages/stream';
const BRANCH = '/api/v1/conversations/branch-1/messages/stream';
const slotOf = (id: string) => useChatStore.getState().slots[id];
const turn = () => useCompareStore.getState().turns[primarySlotId];
let primarySlotId: string;

function newPrimarySlot(): string {
  const id = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(id, { isInitialized: true, hasLoaded: true, isOwner: true });
  useChatStore.setState({ activeSlotId: id });
  return id;
}

const send = (text = 'What was Q3 revenue?') =>
  buildExternalStoreConfig(primarySlotId).onNew!({ role: 'user', content: [{ type: 'text', text }] } as never);

function enableCompare() {
  useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
  useChatStore.getState().setAvailableModelsForCtx(ASSISTANT_CTX, [llm(MODEL_A), llm(MODEL_B)]);
  useCompareStore.setState({ enabled: true, models: [MODEL_A, MODEL_B] });
}

const flush = () => vi.waitFor(() => undefined, { timeout: 50 }).catch(() => undefined);

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  useCompareStore.setState({ enabled: false, models: [], turns: {} });
  useChatStore.setState({ ...initialChat, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  useChatStore.getState().setDefaultModelForCtx(ASSISTANT_CTX, DEFAULT);
  useFeatureFlagsStore.setState({ flags: null });
  useToastStore.getState().clearAll();
  window.history.replaceState(null, '', '/chat/');
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});

  events = [];
  sentStreams = [];
  streams = {};
  fetchMock.mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : (input as Request).url;
    const path = new URL(url, 'http://localhost').pathname;
    sentStreams.push({ path, body: JSON.parse(String(init?.body ?? '{}')), signal: init?.signal });
    events.push(`stream ${path}`);
    const handler = streams[path];
    if (!handler) throw new Error(`no stream planned for ${path}`);
    return handler(init);
  });
  api = fakeApi({
    'POST /api/v1/conversations/conv-1/compare/branch': () => {
      events.push('branch');
      return { status: 200, data: { branchConversationId: 'branch-1' } };
    },
    'POST /api/v1/conversations/conv-1/compare/choose': (config) => {
      events.push(`choose ${JSON.parse(String(config.data)).choice}`);
      return { status: 200, data: { ok: true } };
    },
    'POST /api/v1/conversations/conv-1/cancel': { status: 200, data: { cancelled: true } },
    'POST /api/v1/conversations/branch-1/cancel': { status: 200, data: { cancelled: true } },
    'GET /api/v1/conversations/conv-1/': () => ({
      status: 200,
      data: { conversation: conversation('conv-1', 'Branch answer', MODEL_B), filters: {}, meta: {} },
    }),
  });
  primarySlotId = newPrimarySlot();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Both models answer, and both slots settle. */
async function runToCompletion() {
  streams[PRIMARY] = planOf([turnStarted(), ...finish('conv-1', 'Primary answer', MODEL_A)]);
  streams[BRANCH] = planOf(finish('branch-1', 'Branch answer', MODEL_B));
  await send();
  await vi.waitFor(() => {
    expect(turn()?.branchSlotId).toBeTruthy();
    const b = slotOf(turn()!.branchSlotId!);
    expect(columnStatus(b)).toBe('done');
    expect(columnStatus(slotOf(primarySlotId))).toBe('done');
  });
}

describe('sending in compare mode', () => {
  it('sends the primary with compareTurn and model A, in the default agent mode', async () => {
    enableCompare();
    streams[PRIMARY] = planOf([]);
    await send();
    await vi.waitFor(() => expect(sentStreams.length).toBeGreaterThan(0));
    const { body } = sentStreams[0];
    expect(body).toMatchObject({ compareTurn: true, modelKey: 'ka', modelName: 'model-a', conversationId: 'conv-1' });
    expect(String(body.chatMode)).toMatch(/^agent/);
  });

  it('starts the branch only after turn_started, in model B with the same question', async () => {
    enableCompare();
    const primary = gated([turnStarted()]);
    streams[PRIMARY] = primary.handler;
    streams[BRANCH] = planOf([]);
    await send();
    await flush();
    expect(api.count('POST /api/v1/conversations/conv-1/compare/branch')).toBe(0);

    primary.release();
    await vi.waitFor(() => expect(sentStreams.some((s) => s.path === BRANCH)).toBe(true));

    expect(api.sent.find((r) => r.url.endsWith('/compare/branch'))?.body).toEqual({ userMessageId: 'u1' });
    expect(events.indexOf('branch')).toBeLessThan(events.indexOf(`stream ${BRANCH}`));
    const branch = sentStreams.find((s) => s.path === BRANCH)!.body;
    expect(branch).toMatchObject({
      query: 'What was Q3 revenue?',
      modelKey: 'kb',
      modelName: 'model-b',
      conversationId: 'branch-1',
      chatMode: sentStreams[0].body.chatMode,
    });
    expect(branch).not.toHaveProperty('compareTurn');
  });

  it('streams both slots in parallel and keeps the branch out of the active slot, URL and Recents', async () => {
    enableCompare();
    streams[PRIMARY] = planOf([turnStarted()], { hang: true });
    streams[BRANCH] = planOf([], { hang: true });
    await send();
    await vi.waitFor(() => expect(turn()?.branchSlotId).toBeTruthy());
    const branchSlotId = turn()!.branchSlotId!;
    await vi.waitFor(() => expect(slotOf(branchSlotId).isStreaming).toBe(true));

    expect(slotOf(primarySlotId).isStreaming).toBe(true);
    expect(slotOf(branchSlotId)).toMatchObject({ convId: 'branch-1', isTemp: false });
    expect(useChatStore.getState().activeSlotId).toBe(primarySlotId);
    expect(window.location.search).not.toContain('branch-1');
    const chat = useChatStore.getState();
    expect(chat.conversations.some((c) => c.id === 'branch-1')).toBe(false);
    expect(Object.keys(chat.pendingConversations)).not.toContain(branchSlotId);
  });

  it('does not toast or mark unread when the hidden branch finishes', async () => {
    enableCompare();
    await runToCompletion();
    expect(useChatStore.getState().unreadConversationIds.has('branch-1')).toBe(false);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('shows a column that fails as an error with a calm state, and Retry makes a new branch', async () => {
    enableCompare();
    streams[PRIMARY] = planOf([turnStarted(), ...finish('conv-1', 'Primary answer', MODEL_A)]);
    streams[BRANCH] = planOf([], { failWith: new Error('upstream exploded') });
    await send();
    await vi.waitFor(() => {
      expect(turn()?.branchSlotId).toBeTruthy();
      expect(columnStatus(slotOf(turn()!.branchSlotId!))).toBe('error');
    });
    const firstBranchSlot = turn()!.branchSlotId!;
    expect(columnStatus(slotOf(primarySlotId))).toBe('done');

    streams[BRANCH] = planOf(finish('branch-1', 'Branch answer', MODEL_B));
    controller.retryBranch(primarySlotId);
    await vi.waitFor(() => {
      expect(api.count('POST /api/v1/conversations/conv-1/compare/branch')).toBe(2);
      expect(columnStatus(slotOf(turn()!.branchSlotId!))).toBe('done');
    });
    expect(slotOf(firstBranchSlot)).toBeUndefined();
    expect(sentStreams.filter((s) => s.path === BRANCH)).toHaveLength(2);
  });

  it('marks the branch failed, with Retry available, when it cannot be created', async () => {
    enableCompare();
    fakeApi({ 'POST /api/v1/conversations/conv-1/compare/branch': { status: 409, data: { message: 'busy' } } });
    streams[PRIMARY] = planOf([turnStarted(), ...finish('conv-1', 'Primary answer', MODEL_A)]);
    await send();
    await vi.waitFor(() => expect(turn()?.phase).toBe('failed'));
    expect(turn()?.template).not.toBeNull();
    expect(turn()?.branchSlotId).toBeNull();
  });

  it('drops the turn when the primary ends without ever announcing its question', async () => {
    enableCompare();
    streams[PRIMARY] = planOf(finish('conv-1', 'Primary answer', MODEL_A));
    await send();
    await vi.waitFor(() => expect(slotOf(primarySlotId).isStreaming).toBe(false));
    await vi.waitFor(() => expect(turn()).toBeUndefined());
    expect(api.count('POST /api/v1/conversations/conv-1/compare/branch')).toBe(0);
  });
});

describe('the toggle follows the chat', () => {
  it('switches off when the user starts a new chat', () => {
    enableCompare();
    controller.onNewChat();
    expect(useCompareStore.getState().enabled).toBe(false);
    expect(useCompareStore.getState().models).toEqual([MODEL_A, MODEL_B]);
  });

  it('stays on when only the active slot is created (the first send of a new chat)', () => {
    enableCompare();
    useChatStore.setState({ activeSlotId: null });
    useChatStore.setState({ activeSlotId: useChatStore.getState().createSlot(null) });
    expect(useCompareStore.getState().enabled).toBe(true);
  });

  it('switches off when the user moves from one open chat to another', () => {
    enableCompare();
    useChatStore.setState({ activeSlotId: primarySlotId });
    useChatStore.setState({ activeSlotId: useChatStore.getState().createSlot('conv-other') });
    expect(useCompareStore.getState().enabled).toBe(false);
  });

  it('stays on while the page only gains a query string, and switches off on another path', async () => {
    enableCompare();
    window.history.replaceState(null, '', '/chat/?conversationId=conv-1');
    controller.onLeaveChat('/chat/');
    await new Promise((r) => setTimeout(r, 10));
    expect(useCompareStore.getState().enabled).toBe(true);

    window.history.replaceState(null, '', '/settings');
    controller.onLeaveChat('/chat/');
    await vi.waitFor(() => expect(useCompareStore.getState().enabled).toBe(false));
  });
});

describe('a card in one column', () => {
  it('lets the other column finish and be chosen while one waits on a question card', async () => {
    enableCompare();
    await runToCompletion();
    useChatStore.getState().updateSlot(primarySlotId, {
      pendingAskUserQuestion: { status: 'pending', assistantMessageId: 'a', answers: {}, payload: { name: 'ask_user_question', questions: [] } },
    });
    expect(columnStatus(slotOf(primarySlotId))).toBe('waiting');
    expect(columnStatus(slotOf(turn()!.branchSlotId!))).toBe('done');
    await expect(controller.chooseAnswer(primarySlotId, 'branch')).resolves.toBe(true);
    expect(events).toContain('choose branch-1');
  });

  it('keeps a card follow-up of each column on its own model', async () => {
    enableCompare();
    await runToCompletion();
    const { buildStreamChatRequestForSlot } = await import('../../runtime');
    expect(buildStreamChatRequestForSlot(primarySlotId, 'answer')?.modelKey).toBe('ka');
    expect(buildStreamChatRequestForSlot(turn()!.branchSlotId!, 'answer')?.modelKey).toBe('kb');
  });
});

describe('choosing', () => {
  it('keeps the primary: choose primary, branch slot and turn gone, nothing refetched', async () => {
    enableCompare();
    await runToCompletion();
    const branchSlotId = turn()!.branchSlotId!;
    await expect(controller.chooseAnswer(primarySlotId, 'primary')).resolves.toBe(true);

    expect(events).toContain('choose primary');
    expect(turn()).toBeUndefined();
    expect(slotOf(branchSlotId)).toBeUndefined();
    expect(api.count('GET /api/v1/conversations/conv-1/')).toBe(0);
  });

  it('keeps the branch: choose with its id, then reloads the primary conversation', async () => {
    enableCompare();
    await runToCompletion();
    const branchSlotId = turn()!.branchSlotId!;
    await expect(controller.chooseAnswer(primarySlotId, 'branch')).resolves.toBe(true);

    expect(events).toContain('choose branch-1');
    expect(api.count('GET /api/v1/conversations/conv-1/')).toBe(1);
    const texts = slotOf(primarySlotId).messages.map((m) => JSON.stringify(m.content));
    expect(texts.some((t) => t.includes('Branch answer'))).toBe(true);
    expect(turn()).toBeUndefined();
    expect(slotOf(branchSlotId)).toBeUndefined();
  });

  it('keeps both columns and reports a conflict when the server answers 409', async () => {
    enableCompare();
    await runToCompletion();
    fakeApi({ 'POST /api/v1/conversations/conv-1/compare/choose': { status: 409, data: { message: 'in progress' } } });
    const branchSlotId = turn()!.branchSlotId!;
    await expect(controller.chooseAnswer(primarySlotId, 'branch')).resolves.toBe(false);

    expect(turn()).toMatchObject({ chooseError: 'conflict', choosing: false });
    expect(slotOf(branchSlotId)).toBeDefined();
  });

  it('treats a 404 on choose as already done: refetches the parent and ends the compare', async () => {
    enableCompare();
    await runToCompletion();
    fakeApi({
      'POST /api/v1/conversations/conv-1/compare/choose': { status: 404, data: { message: 'gone' } },
      'GET /api/v1/conversations/conv-1/': {
        status: 200,
        data: { conversation: conversation('conv-1', 'Branch answer', MODEL_B), filters: {}, meta: {} },
      },
    });
    const branchSlotId = turn()!.branchSlotId!;
    await expect(controller.chooseAnswer(primarySlotId, 'branch')).resolves.toBe(true);
    expect(turn()).toBeUndefined();
    expect(slotOf(branchSlotId)).toBeUndefined();
  });

  it('reports a plain failure for other errors and allows trying again', async () => {
    enableCompare();
    await runToCompletion();
    fakeApi({ 'POST /api/v1/conversations/conv-1/compare/choose': { status: 500, data: {} } });
    await expect(controller.chooseAnswer(primarySlotId, 'primary')).resolves.toBe(false);
    expect(turn()?.chooseError).toBe('failed');
  });
});

describe('leaving the comparison', () => {
  it('chooses the primary and drops the branch before the next message goes out', async () => {
    enableCompare();
    await runToCompletion();
    const branchSlotId = turn()!.branchSlotId!;
    events.length = 0;
    streams[PRIMARY] = planOf(finish('conv-1', 'Second answer', MODEL_A));
    await send('And Q4?');

    expect(events[0]).toBe('choose primary');
    expect(events.indexOf('choose primary')).toBeLessThan(events.indexOf(`stream ${PRIMARY}`));
    expect(slotOf(branchSlotId)).toBeUndefined();
  });

  it('stops the branch but lets the primary keep running when the user switches conversation', async () => {
    enableCompare();
    streams[PRIMARY] = planOf([turnStarted()], { hang: true });
    streams[BRANCH] = planOf([], { hang: true });
    await send();
    await vi.waitFor(() => expect(slotOf(turn()?.branchSlotId ?? '')?.isStreaming).toBe(true));
    const branchSlotId = turn()!.branchSlotId!;

    const other = useChatStore.getState().createSlot('conv-other');
    useChatStore.getState().setActiveSlot(other);

    await vi.waitFor(() => expect(turn()).toBeUndefined());
    expect(slotOf(branchSlotId)).toBeUndefined();
    await vi.waitFor(() => {
      expect(api.count('POST /api/v1/conversations/branch-1/cancel')).toBe(1);
    });
    expect(api.count('POST /api/v1/conversations/conv-1/cancel')).toBe(0);
    expect(events).toContain('choose primary');
  });

  it('removes a branch that finished being created after the turn was dropped', async () => {
    enableCompare();
    streams[PRIMARY] = planOf([turnStarted()], { hang: true });
    let createBranch!: () => void;
    const created = new Promise<void>((r) => (createBranch = r));
    fakeApi({
      'POST /api/v1/conversations/conv-1/compare/branch': async () => {
        await created;
        return { status: 200, data: { branchConversationId: 'branch-1' } };
      },
      'POST /api/v1/conversations/conv-1/compare/choose': () => {
        events.push('choose primary');
        return { status: 200, data: { ok: true } };
      },
      'POST /api/v1/conversations/conv-1/cancel': { status: 200, data: {} },
    });
    await send();
    await vi.waitFor(() => expect(turn()?.userMessageId).toBe('u1'));
    useChatStore.getState().setActiveSlot(useChatStore.getState().createSlot('conv-other'));
    await vi.waitFor(() => expect(turn()).toBeUndefined());
    createBranch();
    await vi.waitFor(() => expect(events.filter((e) => e === 'choose primary').length).toBeGreaterThan(0));
    expect(sentStreams.some((s) => s.path === BRANCH)).toBe(false);
  });

  it('discardCompare (regenerate) drops the branch without stopping the primary', async () => {
    enableCompare();
    await runToCompletion();
    controller.discardCompare(primarySlotId);
    await vi.waitFor(() => expect(events).toContain('choose primary'));
    expect(turn()).toBeUndefined();
    expect(api.count('POST /api/v1/conversations/conv-1/cancel')).toBe(0);
  });
});

describe('after a reload', () => {
  const restoreRoutes = (branchUser = 'u1') => {
    fakeApi({
      'GET /api/v1/conversations/conv-1/compare': {
        status: 200,
        data: { branches: [{ branchConversationId: 'branch-1', userMessageId: branchUser }] },
      },
      'GET /api/v1/conversations/branch-1/': {
        status: 200,
        data: { conversation: conversation('branch-1', 'Branch answer', MODEL_B), filters: {}, meta: {} },
      },
    });
  };
  const loadPrimary = () =>
    useChatStore.getState().updateSlot(primarySlotId, {
      rawMessages: conversation('conv-1', 'Primary answer', MODEL_A).messages,
    });

  it('restores the compare view for the last turn from GET compare and loads the branch by id', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
    loadPrimary();
    restoreRoutes();
    await controller.restoreCompare(primarySlotId);

    expect(turn()).toMatchObject({ phase: 'ready', branchConvId: 'branch-1', userMessageId: 'u1' });
    expect(turn()!.models!.map((m) => m.modelKey)).toEqual(['ka', 'kb']);
    const branch = slotOf(turn()!.branchSlotId!);
    expect(branch.convId).toBe('branch-1');
    expect(columnStatus(branch)).toBe('done');
    expect(useChatStore.getState().activeSlotId).toBe(primarySlotId);
    expect(useChatStore.getState().conversations.some((c) => c.id === 'branch-1')).toBe(false);
  });

  it('restores the compare view when question-card answers were added after the compared question', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
    const rows = conversation('conv-1', 'Primary answer', MODEL_A).messages;
    useChatStore.getState().updateSlot(primarySlotId, {
      rawMessages: [
        ...rows,
        { ...rows[0], _id: 'u-card-answer', content: 'User selections: India, 7 days' },
        { ...rows[rows.length - 1], _id: 'b-final', content: 'Final plan' },
      ],
    });
    restoreRoutes();
    await controller.restoreCompare(primarySlotId);

    expect(turn()).toMatchObject({ phase: 'ready', branchConvId: 'branch-1', userMessageId: 'u1' });
  });

  it('does not show a branch left behind by an older question, and discards it', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
    const rows = conversation('conv-1', 'Primary answer', MODEL_A).messages;
    useChatStore.getState().updateSlot(primarySlotId, {
      rawMessages: [
        ...rows,
        { ...rows[0], _id: 'u-next', content: 'A newer question' },
        { ...rows[rows.length - 1], _id: 'b-next', content: 'Its answer' },
      ],
    });
    fakeApi({
      'GET /api/v1/conversations/conv-1/compare': {
        status: 200,
        data: { branches: [{ branchConversationId: 'branch-1', userMessageId: 'u1' }] },
      },
      'POST /api/v1/conversations/conv-1/compare/choose': (config) => {
        events.push(`choose ${JSON.parse(String(config.data)).choice}`);
        return { status: 200, data: { ok: true } };
      },
    });
    await controller.restoreCompare(primarySlotId);

    expect(turn()).toBeUndefined();
    await vi.waitFor(() => expect(events).toContain('choose primary'));
  });

  it('picks the branch that belongs to the current question when an older one is also listed', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
    const rows = conversation('conv-1', 'Primary answer', MODEL_A).messages;
    useChatStore.getState().updateSlot(primarySlotId, {
      rawMessages: [
        ...rows,
        { ...rows[0], _id: 'u-next', content: 'A newer question' },
        { ...rows[rows.length - 1], _id: 'b-next', content: 'Its answer' },
      ],
    });
    fakeApi({
      'GET /api/v1/conversations/conv-1/compare': {
        status: 200,
        data: {
          branches: [
            { branchConversationId: 'branch-old', userMessageId: 'u1' },
            { branchConversationId: 'branch-1', userMessageId: 'u-next' },
          ],
        },
      },
      'GET /api/v1/conversations/branch-1/': {
        status: 200,
        data: { conversation: conversation('branch-1', 'Branch answer', MODEL_B), filters: {}, meta: {} },
      },
    });
    await controller.restoreCompare(primarySlotId);

    expect(turn()).toMatchObject({ branchConvId: 'branch-1', userMessageId: 'u-next' });
  });

  it('restores nothing when there is no branch, the flag is off, or the branch is for an older question', async () => {
    loadPrimary();
    fakeApi({ 'GET /api/v1/conversations/conv-1/compare': { status: 200, data: { branches: [] } } });
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
    await controller.restoreCompare(primarySlotId);
    expect(turn()).toBeUndefined();

    restoreRoutes('u0');
    await controller.restoreCompare(primarySlotId);
    expect(turn()).toBeUndefined();

    restoreRoutes();
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: false } });
    await controller.restoreCompare(primarySlotId);
    expect(turn()).toBeUndefined();
  });

  const branchDetail = (status: string, messages: ConversationMessage[], model = MODEL_B) => ({
    status: 200,
    data: {
      conversation: {
        ...conversation('branch-1', '', model),
        status,
        messages,
      },
      filters: {},
      meta: {},
    },
  });
  const listRoute = {
    'GET /api/v1/conversations/conv-1/compare': {
      status: 200,
      data: { branches: [{ branchConversationId: 'branch-1', userMessageId: 'u1' }] },
    },
  };
  const question = row({ _id: 'u1', messageType: 'user_query', content: 'What was Q3 revenue?' });

  it('keeps refetching a branch whose run is still going until it settles', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
      loadPrimary();
      const answer = conversation('branch-1', 'Late answer', MODEL_B).messages;
      fakeApi({
        ...listRoute,
        'GET /api/v1/conversations/branch-1/': [
          branchDetail('Inprogress', [question]),
          branchDetail('Complete', answer),
        ],
      });
      await controller.restoreCompare(primarySlotId);
      const branchSlotId = turn()!.branchSlotId!;
      expect(slotOf(branchSlotId).remoteRunInProgress).toBe(true);
      expect(columnStatus(slotOf(branchSlotId))).not.toBe('done');

      await vi.advanceTimersByTimeAsync(3100);
      expect(columnStatus(slotOf(branchSlotId))).toBe('done');
      expect(slotOf(branchSlotId).remoteRunInProgress).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('offers Retry for a settled branch that has no answer when the second model is known', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
    loadPrimary();
    fakeApi({ ...listRoute, 'GET /api/v1/conversations/branch-1/': branchDetail('Complete', [question]) });
    await controller.restoreCompare(primarySlotId);
    expect(turn()).toMatchObject({ phase: 'failed', template: { query: 'What was Q3 revenue?', modelKey: 'kb' } });
  });

  it('drops a settled branch with no answer and no known second model, keeping only the primary', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
    loadPrimary();
    const local = fakeApi({
      ...listRoute,
      'GET /api/v1/conversations/branch-1/': branchDetail('Complete', [], MODEL_A),
      'POST /api/v1/conversations/conv-1/compare/choose': { status: 200, data: { ok: true } },
    });
    await controller.restoreCompare(primarySlotId);
    expect(turn()).toBeUndefined();
    await vi.waitFor(() => expect(local.count('POST /api/v1/conversations/conv-1/compare/choose')).toBe(1));
  });

  it('asks the server nothing about branches when the flag is off', async () => {
    loadPrimary();
    const local = fakeApi({});
    await controller.restoreCompare(primarySlotId);
    expect(local.sent).toHaveLength(0);
  });
});

describe('with Compare off, a send is the same as before', () => {
  const stable = (body: Record<string, unknown>) => {
    const { runId: _r, currentTime: _t, timezone: _z, ...rest } = body;
    return rest;
  };

  it('adds no compareTurn key and uses the composer model, flag off', async () => {
    useCompareStore.setState({ enabled: true, models: [MODEL_A, MODEL_B] });
    streams[PRIMARY] = planOf([]);
    await send();
    await vi.waitFor(() => expect(sentStreams.length).toBe(1));
    expect(sentStreams[0].body).not.toHaveProperty('compareTurn');
    expect(sentStreams[0].body).toMatchObject({ modelKey: 'kd', modelName: 'model-d' });
    expect(api.count('POST /api/v1/conversations/conv-1/compare/branch')).toBe(0);
    expect(turn()).toBeUndefined();
  });

  it('builds the same body with the flag on but the toggle off as with the flag off', async () => {
    streams[PRIMARY] = planOf([]);
    await send();
    await vi.waitFor(() => expect(sentStreams.length).toBe(1));
    const flagOff = stable(sentStreams[0].body);

    sentStreams.length = 0;
    primarySlotId = newPrimarySlot();
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
    useChatStore.getState().setAvailableModelsForCtx(ASSISTANT_CTX, [llm(MODEL_A), llm(MODEL_B)]);
    useCompareStore.setState({ enabled: false, models: [MODEL_A, MODEL_B] });
    await send();
    await vi.waitFor(() => expect(sentStreams.length).toBe(1));

    expect(stable(sentStreams[0].body)).toEqual(flagOff);
    expect(Object.keys(flagOff).sort()).toEqual(
      expect.arrayContaining(['query', 'modelKey', 'modelName', 'chatMode', 'conversationId']),
    );
  });

  it('falls back to a normal send for an image-mode composer, a saved agent, a missing model and one picked model', async () => {
    enableCompare();
    const build = (over: Record<string, unknown>) => {
      const request = { query: 'q', chatMode: 'agent:auto', modelKey: 'x', modelName: 'x', modelFriendlyName: 'x', filters: { apps: [], kb: [] }, ...over };
      const applied = controller.applyCompareToRequest(primarySlotId, request as never, 'q');
      return { applied, request };
    };
    expect(build({ chatMode: 'image' }).applied).toBe(false);
    expect(build({ agentId: 'agent-1' }).applied).toBe(false);
    expect(turn()).toBeUndefined();

    useCompareStore.setState({ models: [MODEL_A] });
    expect(build({}).applied).toBe(false);

    useCompareStore.setState({ models: [MODEL_A, { ...MODEL_B, modelName: 'deleted' }] });
    const stale = build({});
    expect(stale.applied).toBe(false);
    expect(stale.request).not.toHaveProperty('compareTurn');
  });
});
