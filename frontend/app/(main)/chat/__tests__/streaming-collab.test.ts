/**
 * Collaborative sends end to end (PR-09d): `streamMessageForSlot` with only `fetch` faked.
 * Covers the idempotency fields, run ids, each 409 code, and merging a finished turn.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  installMemoryStorage,
  jsonResponse,
  jwtExpiringIn,
  sseFrame,
  sseResponse,
} from '@/lib/api/__tests__/sse-response';
import type { ConversationMessage, StreamChatRequest } from '../types';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useChatStore } = await import('../store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { useToastStore } = await import('@/lib/store/toast-store');
const { streamMessageForSlot } = await import('../streaming');
const { CollaborationApi } = await import('../collaboration-api');
const { applyFeedPage } = await import('../utils/apply-feed-page');
const { getThreadMessagePlainText, loadHistoricalMessages } = await import('../runtime');
const { loadDraft, saveDraft } = await import('../utils/draft-storage');
const { FEED_NOT_MODIFIED } = await import('../collaboration-types');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();

const frame = (type: string, fields: Record<string, unknown> = {}) => sseFrame(type, { type, ...fields });

function stored(over: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm', messageType: 'user_query', content: '', contentFormat: 'MARKDOWN', citations: [],
    followUpQuestions: [], feedback: [], createdAt: '2026-10-01T00:00:00.000Z', updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  } as ConversationMessage;
}

const base: ConversationMessage[] = [
  stored({ _id: 'u1', content: 'first question', seq: 0 }),
  stored({ _id: 'a1', messageType: 'bot_response', content: 'first answer', seq: 1 }),
];

function conversation(messages: ConversationMessage[]) {
  return {
    _id: 'conv-1', title: 't', createdAt: 'x', updatedAt: 'x', isShared: true, status: 'complete',
    modelInfo: { modelKey: 'm1', modelName: 'gpt-5' }, messages,
  };
}

function request(over: Partial<StreamChatRequest> = {}): StreamChatRequest {
  return {
    query: 'mine', modelKey: 'm1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5', chatMode: 'quick',
    filters: { apps: [], kb: [] }, ...over,
  } as StreamChatRequest;
}

function setFlag(on: boolean) {
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: on } } as never);
}

function openSlot(collaborative = true) {
  const slotId = useChatStore.getState().createSlot('conv-1');
  const rows = collaborative
    ? loadHistoricalMessages(base, { rev: 1 }).messages
    : loadHistoricalMessages(base).messages;
  useChatStore.getState().updateSlot(slotId, {
    messages: rows,
    isInitialized: true,
    rev: collaborative ? 1 : null,
    access: {
      role: 'write', isOwner: false, accessLevel: 'write', canSend: true, canManage: false, canInvite: false,
      isCollaborative: collaborative,
    },
  });
  useChatStore.setState({ activeSlotId: slotId });
  return slotId;
}

const slot = (id: string) => useChatStore.getState().slots[id];
const rowsOf = (id: string) => slot(id).messages.map((m) => [m.role, getThreadMessagePlainText(m)]);
const sentBody = (call = 0) => JSON.parse(String(fetchMock.mock.calls[call][1]?.body));

function conflict(code: string, details: Record<string, unknown>) {
  fetchMock.mockResolvedValueOnce(jsonResponse(409, { error: { code, message: code, details } }));
}

function finished(messages: ConversationMessage[]) {
  return sseResponse([frame('RUN_FINISHED', { result: { conversation: conversation(messages) } })]);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  useToastStore.getState().clearAll();
  window.localStorage.clear();
  setFlag(true);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('what a send carries', () => {
  it('adds clientMessageId and the highest seq it has seen as baseSeq', async () => {
    const slotId = openSlot();
    fetchMock.mockResolvedValueOnce(finished(base));
    await streamMessageForSlot(slotId, 'mine', request());
    expect(sentBody().clientMessageId).toMatch(/^[0-9a-f-]{36}$/);
    expect(sentBody().baseSeq).toBe(1);
  });

  it('makes a new id per send and reads baseSeq when the send starts', async () => {
    const slotId = openSlot();
    fetchMock.mockResolvedValueOnce(finished(base));
    await streamMessageForSlot(slotId, 'one', request());
    const first = sentBody();
    useChatStore.getState().updateSlot(slotId, {
      messages: [...slot(slotId).messages, { id: 'x', role: 'user', content: [], metadata: { custom: { seq: 7 } } }],
    });
    fetchMock.mockResolvedValueOnce(finished(base));
    await streamMessageForSlot(slotId, 'two', request());
    expect(sentBody(1).clientMessageId).not.toBe(first.clientMessageId);
    expect(first.baseSeq).toBe(1);
    expect(sentBody(1).baseSeq).toBe(7);
  });

  it('keeps ids the caller already set and omits baseSeq when no row has a seq', async () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    fetchMock.mockResolvedValueOnce(finished([]));
    await streamMessageForSlot(slotId, 'mine', request({ clientMessageId: 'mine-1' }));
    expect(sentBody().clientMessageId).toBe('mine-1');
    expect(sentBody()).not.toHaveProperty('baseSeq');
  });

  it('marks the optimistic rows so a poll can reconcile them', async () => {
    const slotId = openSlot();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    fetchMock.mockImplementationOnce(async () => {
      await gate;
      return finished(base);
    });
    const run = streamMessageForSlot(slotId, 'mine', request({ clientMessageId: 'cm-9' }));
    const rows = slot(slotId).messages;
    expect(rows[2].metadata?.custom).toMatchObject({ clientMessageId: 'cm-9', pending: true });
    expect(rows[3].metadata?.custom).toMatchObject({ pending: true });
    release();
    await run;
  });

  it('sends nothing new in a solo chat with the flag on', async () => {
    const slotId = openSlot(false);
    fetchMock.mockResolvedValueOnce(finished(base));
    const run = streamMessageForSlot(slotId, 'mine', request());
    expect(slot(slotId).messages[2].metadata?.custom).not.toHaveProperty('clientMessageId');
    expect(slot(slotId).messages[2].metadata?.custom).not.toHaveProperty('author');
    await run;
    expect(sentBody()).not.toHaveProperty('clientMessageId');
    expect(sentBody()).not.toHaveProperty('baseSeq');
  });

  it('sends nothing new with the flag off', async () => {
    setFlag(false);
    const slotId = openSlot(false);
    fetchMock.mockResolvedValueOnce(finished(base));
    const run = streamMessageForSlot(slotId, 'mine', request());
    expect(slot(slotId).messages[2].metadata?.custom).not.toHaveProperty('clientMessageId');
    expect(slot(slotId).messages[3].metadata).toBeUndefined();
    await run;
    expect(sentBody()).not.toHaveProperty('clientMessageId');
    expect(sentBody()).not.toHaveProperty('baseSeq');
  });
});

describe('run ids', () => {
  it('adopts the X-Run-Id header over the client-made id', async () => {
    const slotId = openSlot();
    fetchMock.mockImplementationOnce(async (_u, init) =>
      sseResponse(
        { chunks: [frame('TEXT_MESSAGE_START')], hang: true, signal: init?.signal },
        { headers: { 'Content-Type': 'text/event-stream', 'X-Run-Id': 'server-run' } },
      ),
    );
    const run = streamMessageForSlot(slotId, 'mine', request());
    expect(slot(slotId).runId).toMatch(/^[0-9a-f-]{36}$/);
    await vi.waitFor(() => expect(slot(slotId).runId).toBe('server-run'));
    slot(slotId).abortController?.abort();
    await run;
  });

  it('adopts runId from the first frame when there is no header', async () => {
    const slotId = openSlot();
    fetchMock.mockImplementationOnce(async (_u, init) =>
      sseResponse({
        chunks: [frame('CUSTOM', { name: 'conversation_created', value: { runId: 'frame-run' } })],
        hang: true,
        signal: init?.signal,
      }),
    );
    const run = streamMessageForSlot(slotId, 'mine', request());
    await vi.waitFor(() => expect(slot(slotId).runId).toBe('frame-run'));
    slot(slotId).abortController?.abort();
    await run;
  });

  it('keeps its own id with the flag off', async () => {
    setFlag(false);
    const slotId = openSlot(false);
    fetchMock.mockImplementationOnce(async (_u, init) =>
      sseResponse(
        { chunks: [frame('CUSTOM', { name: 'conversation_created', value: { runId: 'frame-run' } })], hang: true, signal: init?.signal },
        { headers: { 'Content-Type': 'text/event-stream', 'X-Run-Id': 'server-run' } },
      ),
    );
    const run = streamMessageForSlot(slotId, 'mine', request());
    const own = slot(slotId).runId;
    await new Promise((r) => setTimeout(r, 20));
    expect(slot(slotId).runId).toBe(own);
    slot(slotId).abortController?.abort();
    await run;
  });
});

describe('409 CONVERSATION_BUSY', () => {
  it('records who is running, rolls the rows back, queues the message and shows no toast', async () => {
    const slotId = openSlot();
    const run = { userId: 'u2', displayName: 'Bob', startedAt: '2026-10-01T00:00:00.000Z' };
    conflict('CONVERSATION_BUSY', { activeRun: run });
    await streamMessageForSlot(slotId, 'mine', request());
    const s = slot(slotId);
    expect(s.activeRun).toMatchObject(run);
    expect(s.rev).toBeNull();
    expect(s.isStreaming).toBe(false);
    expect(s.runId).toBeNull();
    expect(rowsOf(slotId)).toEqual([['user', 'first question'], ['assistant', 'first answer']]);
    expect(s.queuedSend).toMatchObject({ query: 'mine', clientMessageId: sentBody().clientMessageId });
    expect(loadDraft('conv-1')).toBe('mine');
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('keeps the mentions of the rejected message in the queue', async () => {
    const slotId = openSlot();
    const mentions = [{ type: 'assistant' as const, id: 'self' }, { type: 'user' as const, id: 'u-bob' }];
    conflict('CONVERSATION_BUSY', { activeRun: { userId: 'u2', displayName: 'Bob', startedAt: '2026-10-01T00:00:00.000Z' } });
    await streamMessageForSlot(slotId, '@assistant ask <@user:u-bob>', request({ mentions }));
    expect(sentBody().mentions).toEqual(mentions);
    expect(slot(slotId).queuedSend?.mentions).toEqual(mentions);
  });

  it('still queues when the server names no usable run', async () => {
    const slotId = openSlot();
    conflict('CONVERSATION_BUSY', {});
    await streamMessageForSlot(slotId, 'mine', request());
    expect(slot(slotId).activeRun).not.toBeNull();
    expect(slot(slotId).queuedSend).not.toBeNull();
  });

  it('is an ordinary error with the flag off', async () => {
    setFlag(false);
    const slotId = openSlot(false);
    conflict('CONVERSATION_BUSY', { activeRun: { userId: 'u2', displayName: 'Bob', startedAt: 'x' } });
    await streamMessageForSlot(slotId, 'mine', request());
    expect(slot(slotId).activeRun).toBeNull();
    expect(slot(slotId).queuedSend).toBeNull();
    expect(slot(slotId).messages.length).toBeGreaterThan(2);
  });
});

describe('409 CONVERSATION_BUSY on a card answer', () => {
  it('keeps the card answerable, records the run, says why and does not queue', async () => {
    const slotId = openSlot();
    const answers = { q1: { selected: ['a'] } };
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: { status: 'answered', answers, toolCallMessageId: 'tc1' } as never,
    });
    const run = { userId: 'u2', displayName: 'Bob', startedAt: '2026-10-01T00:00:00.000Z' };
    conflict('CONVERSATION_BUSY', { activeRun: run });
    await streamMessageForSlot(slotId, 'User selections: a', request({ resume: { toolCallMessageId: 'tc1' } }), {
      resumeAskUserQuestion: true,
    });
    const s = slot(slotId);
    expect(s.pendingAskUserQuestion).toMatchObject({ status: 'pending', answers });
    expect(s.activeRun).toMatchObject(run);
    expect(s.queuedSend ?? null).toBeNull();
    expect(s.isStreaming).toBe(false);
    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(useToastStore.getState().toasts[0].variant).toBe('error');
  });
});

describe('409 CONVERSATION_CHANGED', () => {
  it('fetches and merges the newer turns, keeps the message to resend, and counts them', async () => {
    const slotId = openSlot();
    const fetchFeed = vi.spyOn(CollaborationApi, 'fetchFeed').mockResolvedValue({
      messages: [
        { ...stored({ _id: 'u5', content: 'theirs', seq: 2 }), author: { userId: 'u2', displayName: 'Bob' } },
        { ...stored({ _id: 'a5', messageType: 'bot_response', content: 'their answer', seq: 3 }) },
      ],
      rev: 4, nextSeq: 3, hasMore: false, activeRun: null, lastActivityAt: 1,
    } as never);
    conflict('CONVERSATION_CHANGED', { newerCount: 2 });
    await streamMessageForSlot(slotId, 'mine', request());
    await vi.waitFor(() => expect(slot(slotId).messages).toHaveLength(4));
    const s = slot(slotId);
    expect(fetchFeed.mock.calls[0][1]).toEqual({ afterSeq: 1, rev: null });
    expect(rowsOf(slotId)).toEqual([
      ['user', 'first question'], ['assistant', 'first answer'], ['user', 'theirs'], ['assistant', 'their answer'],
    ]);
    expect(s.changedNotice).toMatchObject({ count: 2, pending: { query: 'mine' } });
    expect(s.queuedSend).toBeNull();
    expect(s.rev).toBe(4);
    expect(loadDraft('conv-1')).toBe('mine');
  });

  it('clears the notice when the next send starts', async () => {
    const slotId = openSlot();
    useChatStore.getState().updateSlot(slotId, { changedNotice: { count: 1, pending: null } });
    fetchMock.mockResolvedValueOnce(finished(base));
    const run = streamMessageForSlot(slotId, 'mine', request());
    expect(slot(slotId).changedNotice).toBeNull();
    await run;
  });

  it('leaves the rows alone when the feed says nothing changed', async () => {
    const slotId = openSlot();
    const fetchFeed = vi.spyOn(CollaborationApi, 'fetchFeed').mockResolvedValue(FEED_NOT_MODIFIED);
    conflict('CONVERSATION_CHANGED', { newerCount: 1 });
    await streamMessageForSlot(slotId, 'mine', request());
    await vi.waitFor(() => expect(fetchFeed).toHaveBeenCalled());
    expect(slot(slotId).messages).toHaveLength(2);
  });
});

describe('409 DUPLICATE_MESSAGE', () => {
  it.each([true, false])('reconciles instead of resending (answered=%s)', async (answered) => {
    const slotId = openSlot();
    saveDraft('conv-1', 'mine');
    const fetchFeed = vi.spyOn(CollaborationApi, 'fetchFeed').mockResolvedValue({
      messages: [
        stored({ _id: 'u9', content: 'mine', seq: 2, clientMessageId: 'cm-same' }),
        ...(answered ? [stored({ _id: 'a9', messageType: 'bot_response', content: 'the answer', seq: 3 })] : []),
      ],
      rev: 6, nextSeq: 3, hasMore: false, activeRun: null, lastActivityAt: 1,
    } as never);
    conflict('DUPLICATE_MESSAGE', { messageId: 'u9', answered });
    await streamMessageForSlot(slotId, 'mine', request({ clientMessageId: 'cm-same' }));
    await vi.waitFor(() => expect(slot(slotId).messages.length).toBe(answered ? 4 : 3));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    expect(rowsOf(slotId).filter(([, t]) => t === 'mine')).toHaveLength(1);
    expect(slot(slotId).queuedSend).toBeNull();
    expect(slot(slotId).changedNotice).toBeNull();
    expect(loadDraft('conv-1')).toBeNull();
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('leaves the next poll to recover when the refresh fails', async () => {
    const slotId = openSlot();
    const fetchFeed = vi.spyOn(CollaborationApi, 'fetchFeed').mockRejectedValue({ statusCode: 503 });
    conflict('DUPLICATE_MESSAGE', { messageId: 'u9', answered: false });
    await streamMessageForSlot(slotId, 'mine', request());
    await vi.waitFor(() => expect(fetchFeed).toHaveBeenCalled());
    expect(slot(slotId).accessLost).toBe(false);
    expect(slot(slotId).rev).toBeNull();
  });

  it('marks access lost when the refresh answers 404', async () => {
    const slotId = openSlot();
    vi.spyOn(CollaborationApi, 'fetchFeed').mockRejectedValue({ statusCode: 404 });
    conflict('DUPLICATE_MESSAGE', { messageId: 'u9', answered: true });
    await streamMessageForSlot(slotId, 'mine', request());
    await vi.waitFor(() => expect(slot(slotId).accessLost).toBe(true));
  });
});

describe('other failures', () => {
  it('on a coded refusal (403) leaves no local-only bubble: rows rolled back, text back in the composer, reason shown', async () => {
    const slotId = openSlot();
    const before = slot(slotId).messages;
    fetchMock.mockResolvedValueOnce(jsonResponse(403, { error: { code: 'RESUME_NOT_ALLOWED', message: 'not yours' } }));
    await streamMessageForSlot(slotId, 'my words', request());
    expect(loadDraft('conv-1')).toBe('my words');
    expect(slot(slotId).messages).toEqual(before);
    expect(slot(slotId).composerRestore).toBe('my words');
    expect(slot(slotId).isStreaming).toBe(false);
    expect(useToastStore.getState().toasts.map((t) => t.title)).toContain(
      'Only the person who was asked can answer this question.',
    );
  });

  it('keeps the error row for an uncoded failure, as before', async () => {
    const slotId = openSlot();
    fetchMock.mockResolvedValueOnce(jsonResponse(500, { message: 'boom' }));
    await streamMessageForSlot(slotId, 'my words', request());
    expect(slot(slotId).messages.length).toBeGreaterThan(2);
  });
});

describe('finishing a collaborative turn', () => {
  it('keeps the other person\'s rows that landed meanwhile, in seq order, with no duplicate of the sender\'s own (FE-03)', async () => {
    const slotId = openSlot();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    fetchMock.mockImplementationOnce(async (_u, init) => {
      const clientMessageId = JSON.parse(String(init?.body)).clientMessageId;
      await gate;
      return finished([
        ...base,
        stored({ _id: 'u5', content: 'theirs', seq: 2 }),
        stored({ _id: 'a5', messageType: 'bot_response', content: 'their answer', seq: 3 }),
        stored({ _id: 'u6', content: 'mine', seq: 4, clientMessageId }),
        stored({ _id: 'a6', messageType: 'bot_response', content: 'my answer', seq: 5 }),
      ]);
    });
    const run = streamMessageForSlot(slotId, 'mine', request());

    applyFeedPage(slotId, {
      messages: [
        stored({ _id: 'u5', content: 'theirs', seq: 2 }),
        stored({ _id: 'a5', messageType: 'bot_response', content: 'their answer', seq: 3 }),
      ] as never,
      rev: 2, nextSeq: 3, hasMore: false, activeRun: null, lastActivityAt: 1,
    });
    expect(rowsOf(slotId)).toEqual([
      ['user', 'first question'], ['assistant', 'first answer'], ['user', 'theirs'], ['assistant', 'their answer'],
      ['user', 'mine'], ['assistant', ''],
    ]);

    release();
    await run;
    expect(rowsOf(slotId)).toEqual([
      ['user', 'first question'], ['assistant', 'first answer'], ['user', 'theirs'], ['assistant', 'their answer'],
      ['user', 'mine'], ['assistant', 'my answer'],
    ]);
    expect(slot(slotId).isStreaming).toBe(false);
    expect(slot(slotId).messages.map((m) => m.metadata?.custom?.seq)).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it('does not duplicate the sender\'s row when the server does not echo clientMessageId', async () => {
    const slotId = openSlot();
    fetchMock.mockResolvedValueOnce(
      finished([
        ...base,
        stored({ _id: 'u6', content: 'mine', seq: 2 }),
        stored({ _id: 'a6', messageType: 'bot_response', content: 'my answer', seq: 3 }),
      ]),
    );
    await streamMessageForSlot(slotId, 'mine', request());
    expect(rowsOf(slotId).map(([, t]) => t)).toEqual(['first question', 'first answer', 'mine', 'my answer']);
  });

  it('clears a saved draft on success', async () => {
    const slotId = openSlot();
    saveDraft('conv-1', 'mine');
    fetchMock.mockResolvedValueOnce(finished(base));
    await streamMessageForSlot(slotId, 'mine', request());
    expect(loadDraft('conv-1')).toBeNull();
  });

  it('replaces the list as before in a solo chat', async () => {
    const slotId = openSlot(false);
    fetchMock.mockResolvedValueOnce(finished(base));
    await streamMessageForSlot(slotId, 'mine', request());
    expect(rowsOf(slotId)).toEqual([['user', 'first question'], ['assistant', 'first answer']]);
  });
});

describe('the first send of a new chat that has draft collaborators (M2)', () => {
  let draftStore: typeof import('../draft-share-store').useDraftShareStore;
  let draftBody: typeof import('../draft-share-store').draftShareBody;
  const SHARE = { collaborators: [{ principalType: 'user' as const, principalId: 'u-dana', accessLevel: 'write' as const }], note: 'hi team' };

  beforeEach(async () => {
    ({ useDraftShareStore: draftStore, draftShareBody: draftBody } = await import('../draft-share-store'));
    draftStore.getState().clear();
    draftStore.getState().add([{ type: 'user', id: 'u-dana', name: 'Dana', level: 'write' }], { message: 'hi team' });
  });

  function newChatSlot() {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.setState({ activeSlotId: slotId });
    return slotId;
  }

  it('puts share in the body of the stream that creates the chat, and clears the draft once it completes', async () => {
    const slotId = newChatSlot();
    fetchMock.mockResolvedValueOnce(finished(base));
    await streamMessageForSlot(slotId, 'first words', request({ share: draftBody() }));
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/v1/conversations/stream');
    expect(sentBody().share).toEqual(SHARE);
    expect(draftStore.getState().principals).toEqual([]);
  });

  it('on a refusal of the share keeps the draft, puts the text back and shows the server reason', async () => {
    const slotId = newChatSlot();
    fetchMock.mockResolvedValueOnce(jsonResponse(422, { error: { code: 'INVALID_PRINCIPAL', message: 'nope' } }));
    await streamMessageForSlot(slotId, 'first words', request({ share: draftBody() }));
    expect(draftStore.getState().principals.map((p) => p.id)).toEqual(['u-dana']);
    expect(draftStore.getState().message).toBe('hi team');
    expect(slot(slotId).composerRestore).toBe('first words');
    expect(slot(slotId).isStreaming).toBe(false);
    expect(useToastStore.getState().toasts.map((t) => t.title)).toContain("One of the people or teams can't be added.");
  });
});

describe('the guest agent that answered, on the stream that created the answer (M2)', () => {
  const answer = (extra: Partial<ConversationMessage> = {}) => [
    ...base,
    stored({ _id: 'u2', content: 'joke please', seq: 2 }),
    stored({ _id: 'a2', messageType: 'bot_response', content: 'a joke', seq: 3, ...extra }),
  ];
  const lastCustom = (id: string) => slot(id).messages.at(-1)?.metadata?.custom as Record<string, unknown>;

  it('stamps the mentioned agent from what the picker knew when the final frame does not name it', async () => {
    const slotId = openSlot();
    const { useParticipantsStore } = await import('../mentions/participants-store');
    useParticipantsStore.getState().mergeAgents('conv-1', [{ id: 'ag-1', label: 'Joke Buddy', handle: 'joke-buddy' }]);
    fetchMock.mockResolvedValueOnce(finished(answer()));
    await streamMessageForSlot(slotId, 'joke please', request({ mentions: [{ type: 'agent', id: 'ag-1' }] }));
    expect(lastCustom(slotId).respondingAgent).toEqual({ key: 'ag-1', name: 'Joke Buddy', handle: 'joke-buddy' });
  });

  it('keeps what the server sent, and adds nothing without an agent mention or for the chat’s own agent', async () => {
    const slotId = openSlot();
    fetchMock.mockResolvedValueOnce(finished(answer({ respondingAgent: { key: 'ag-9', name: 'Server Name' } })));
    await streamMessageForSlot(slotId, 'x', request({ mentions: [{ type: 'agent', id: 'ag-1' }] }));
    expect(lastCustom(slotId).respondingAgent).toEqual({ key: 'ag-9', name: 'Server Name' });

    const second = openSlot();
    fetchMock.mockResolvedValueOnce(finished(answer()));
    await streamMessageForSlot(second, 'x', request({ mentions: [{ type: 'user', id: 'u1' }] }));
    expect(lastCustom(second)).not.toHaveProperty('respondingAgent');

    const third = openSlot();
    fetchMock.mockResolvedValueOnce(finished(answer()));
    await streamMessageForSlot(third, 'x', request({ agentId: 'ag-1', mentions: [{ type: 'agent', id: 'ag-1' }] }));
    expect(lastCustom(third)).not.toHaveProperty('respondingAgent');
  });
});
