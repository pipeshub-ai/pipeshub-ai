/**
 * The questions queue with the default chat extension: a question asked while this tab's answer streams waits its
 * turn, first in first out, up to ten; each can be edited back into the box, removed, retried or cleared; the tab keeps
 * a copy across a reload; deleting or archiving the chat drops it.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import '@/lib/__tests__/test-i18n';

installMemoryStorage();

const streamed = vi.hoisted(() => ({
  calls: [] as Array<{ slotId: string; query: string; request: Record<string, unknown> }>,
  /** Set once the store is loaded: the mock can't import it (the store loads streaming). */
  started: (_slotId: string) => {},
}));

// The default extension, read once loaded: importing it from the factory would load the chat modules against the real
// `@/config` instead of this one.
const config = vi.hoisted(() => ({ chatExtension: null as unknown, chatSlots: null as unknown }));
vi.mock('@/config', () => ({
  useAuthStore: { getState: () => ({}) },
  logoutAndRedirect: vi.fn(),
  get chatExtension() {
    return config.chatExtension;
  },
  get chatSlots() {
    return config.chatSlots;
  },
}));
vi.mock('@/app/(main)/chat/streaming', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/chat/streaming')>()),
  // As the real one does before its first await: the slot is streaming at once.
  streamMessageForSlot: (slotId: string, query: string, request: Record<string, unknown>) => {
    streamed.calls.push({ slotId, query, request });
    streamed.started(slotId);
    return Promise.resolve();
  },
}));
vi.mock('@/app/(main)/chat/runtime', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/chat/runtime')>()),
  buildStreamChatRequestForSlot: (_slotId: string, query: string) => ({ query }),
}));

const { useChatStore } = await import('@/app/(main)/chat/store');
streamed.started = (slotId) => useChatStore.getState().updateSlot(slotId, { isStreaming: true });
const { useToastStore } = await import('@/lib/store/toast-store');
const { defaultChatExtension, defaultChatSlots } = await import('../../chat-extension');
config.chatExtension = defaultChatExtension;
config.chatSlots = defaultChatSlots;
const { queueMayGo, sendNextQueued } = await import('../send-when-free');
const {
  MAX_QUEUED_SENDS,
  clearQueue,
  dropQueuedFor,
  editQueued,
  heldSendsOf,
  keepHeldSends,
  queueOf,
  queueSend,
  removeQueued,
  requeueFirst,
  retryQueued,
  shouldQueueSend,
} = await import('../queued-send');
const { useSendWhenFree } = await import('../../hooks/use-send-when-free');
const { useQueuedSendComposer } = await import('../../hooks/use-queued-send-composer');

const initialState = useChatStore.getState();
const FILE = { recordId: 'r1', recordName: 'q3.pdf', mimeType: 'application/pdf', extension: 'pdf', virtualRecordId: 'v1' };

/** A chat of yours whose answer is streaming. */
function streamingChat(convId = 'conv-1') {
  const slotId = useChatStore.getState().createSlot(convId);
  useChatStore.getState().updateSlot(slotId, { isInitialized: true, isOwner: true, isStreaming: true });
  useChatStore.setState({ activeSlotId: slotId });
  return slotId;
}

const slot = (id: string) => useChatStore.getState().slots[id];
const queries = (id: string) => queueOf(slot(id)).map((q) => q.query);
const answerEnds = (id: string) => useChatStore.getState().updateSlot(id, { isStreaming: false });

beforeEach(() => {
  streamed.calls.length = 0;
  window.sessionStorage.clear();
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  useToastStore.setState({ toasts: [] });
});

describe('when a question waits', () => {
  it('waits while your answer streams, not once it has stopped or ended', () => {
    const slotId = streamingChat();
    expect(shouldQueueSend(slot(slotId))).toBe(true);
    useChatStore.getState().updateSlot(slotId, { stopping: true });
    expect(shouldQueueSend(slot(slotId))).toBe(false);
    useChatStore.getState().updateSlot(slotId, { isStreaming: false, stopping: false });
    expect(shouldQueueSend(slot(slotId))).toBe(false);
    expect(shouldQueueSend(undefined)).toBe(false);
  });

  it('goes behind questions already waiting, even with nothing running', () => {
    const slotId = streamingChat();
    queueSend(slotId, { query: 'first' });
    answerEnds(slotId);
    requeueFirst(slotId, queueOf(slot(slotId))[0], 'Refused.');
    expect(shouldQueueSend(slot(slotId))).toBe(true);
  });

  it('never in a chat shared with you to read: it has no box, and nothing it held comes back', () => {
    const slotId = streamingChat();
    useChatStore.getState().updateSlot(slotId, { isOwner: false });
    expect(shouldQueueSend(slot(slotId))).toBe(false);
    expect(renderHook(() => defaultChatSlots.useChatAccess(slotId)).result.current.showComposer).toBe(false);
    keepHeldSends('conv-1', [{ query: 'kept', clientMessageId: 'c-1', queuedAt: 1 }]);
    renderHook(() => useSendWhenFree(slotId));
    expect(queueOf(slot(slotId))).toEqual([]);
  });
});

describe('the queue', () => {
  it('keeps questions in the order asked, with their files, each with its own id', () => {
    const slotId = streamingChat();
    expect(queueSend(slotId, { query: 'one', attachments: [FILE] })).toBe(true);
    expect(queueSend(slotId, { query: 'two', attachments: [] })).toBe(true);
    const [first, second] = queueOf(slot(slotId));
    expect(queries(slotId)).toEqual(['one', 'two']);
    expect(first.attachments).toEqual([FILE]);
    expect(second).not.toHaveProperty('attachments');
    expect(first.clientMessageId).not.toBe(second.clientMessageId);
  });

  it(`takes ${MAX_QUEUED_SENDS} at most; one more goes back to the box with a toast`, () => {
    const slotId = streamingChat();
    for (let i = 0; i < MAX_QUEUED_SENDS; i += 1) expect(queueSend(slotId, { query: `q ${i}` })).toBe(true);
    expect(queueSend(slotId, { query: 'one too many' })).toBe(false);
    expect(queueOf(slot(slotId))).toHaveLength(MAX_QUEUED_SENDS);
    expect(slot(slotId).composerRestore).toBe('one too many');
    expect(useToastStore.getState().toasts.map((t) => t.title)).toContain(
      `You can queue up to ${MAX_QUEUED_SENDS} messages. Wait for one to be sent, or remove one.`,
    );
  });

  it('removes one, clears all, and edits one back into the box', () => {
    const slotId = streamingChat();
    ['a', 'b', 'c'].forEach((query) => queueSend(slotId, { query }));
    const [a, b] = queueOf(slot(slotId));
    removeQueued(slotId, a.clientMessageId);
    expect(queries(slotId)).toEqual(['b', 'c']);
    editQueued(slotId, b.clientMessageId);
    expect(queries(slotId)).toEqual(['c']);
    expect(slot(slotId).composerRestore).toBe('b');
    clearQueue(slotId);
    expect(queueOf(slot(slotId))).toEqual([]);
  });

  it('waits on a refused question until it is retried', () => {
    const slotId = streamingChat();
    queueSend(slotId, { query: 'refused' });
    queueSend(slotId, { query: 'behind it' });
    answerEnds(slotId);
    requeueFirst(slotId, queueOf(slot(slotId))[0], 'PipesHub is busy right now.');
    expect(queueOf(slot(slotId))[0]).toMatchObject({ query: 'refused', failed: 'PipesHub is busy right now.' });
    expect(queueMayGo(slot(slotId))).toBe(false);
    expect(sendNextQueued(slotId)).toBe(false);

    retryQueued(slotId, queueOf(slot(slotId))[0].clientMessageId);
    expect(queueOf(slot(slotId))[0]).not.toHaveProperty('failed');
    expect(sendNextQueued(slotId)).toBe(true);
    expect(streamed.calls.map((c) => c.query)).toEqual(['refused']);
  });
});

describe('sending what waits', () => {
  it('sends the next question only once the answer ahead of it has ended, one at a time, in order', () => {
    const slotId = streamingChat();
    queueSend(slotId, { query: 'first', attachments: [FILE] });
    queueSend(slotId, { query: 'second' });
    renderHook(() => useSendWhenFree(slotId));
    expect(streamed.calls).toHaveLength(0);

    act(() => answerEnds(slotId));
    expect(streamed.calls.map((c) => c.query)).toEqual(['first']);
    expect(streamed.calls[0].request).toEqual({ query: 'first', attachments: [FILE] });
    expect(queries(slotId)).toEqual(['second']);

    act(() => answerEnds(slotId));
    expect(streamed.calls.map((c) => c.query)).toEqual(['first', 'second']);
    expect(queueOf(slot(slotId))).toEqual([]);
  });

  it('holds while a run left from before a reload is still writing the chat', () => {
    const slotId = streamingChat();
    queueSend(slotId, { query: 'later' });
    useChatStore.getState().updateSlot(slotId, { isStreaming: false, remoteRunInProgress: true });
    expect(queueMayGo(slot(slotId))).toBe(false);
  });
});

describe('the tab keeps its queue', () => {
  it('across a reload: a new slot for the chat takes it back and sends it once free', () => {
    const before = streamingChat();
    queueSend(before, { query: 'survives' });
    const hook = renderHook(() => useSendWhenFree(before));
    expect(heldSendsOf('conv-1').map((q) => q.query)).toEqual(['survives']);
    hook.unmount();

    // The reload: a fresh store, the same tab.
    useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null });
    const after = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().updateSlot(after, { isInitialized: true, isOwner: true, isStreaming: true });
    renderHook(() => useSendWhenFree(after));
    expect(queries(after)).toEqual(['survives']);

    act(() => answerEnds(after));
    expect(streamed.calls.map((c) => c.query)).toEqual(['survives']);
    expect(heldSendsOf('conv-1')).toEqual([]);
  });

  it("a reload that may not restore yet keeps the tab's copy, and takes it back once it may", () => {
    keepHeldSends('conv-1', [{ query: 'survives', clientMessageId: 'c-1', queuedAt: 1 }]);
    // An extension may decide only once its settings load: until then this slot says it does not restore.
    const after = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().updateSlot(after, { isInitialized: true, isOwner: false, isStreaming: true });
    renderHook(() => useSendWhenFree(after));
    expect(heldSendsOf('conv-1').map((q) => q.query)).toEqual(['survives']);

    act(() => useChatStore.getState().updateSlot(after, { isOwner: true }));
    expect(queries(after)).toEqual(['survives']);
  });

  it('a reload that cuts the answer short sends nothing: the question stays queued, and the tab keeps its copy', () => {
    vi.useFakeTimers();
    const slotId = streamingChat();
    queueSend(slotId, { query: 'survives the reload' });
    renderHook(() => useSendWhenFree(slotId));
    act(() => {
      window.dispatchEvent(new Event('beforeunload'));
      answerEnds(slotId);
    });
    expect(streamed.calls).toEqual([]);
    expect(queries(slotId)).toEqual(['survives the reload']);
    expect(heldSendsOf('conv-1').map((q) => q.query)).toEqual(['survives the reload']);
    act(() => vi.advanceTimersByTime(2000));
    vi.useRealTimers();
  });

  it('works without storage, never throwing', () => {
    const spy = vi.spyOn(window, 'sessionStorage', 'get').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => keepHeldSends('conv-1', [{ query: 'x', clientMessageId: 'c', queuedAt: 1 }])).not.toThrow();
    expect(heldSendsOf('conv-1')).toEqual([]);
    spy.mockRestore();
  });

  it('drops it when the chat is deleted or archived, so it never goes out on a return', () => {
    const slotId = streamingChat();
    queueSend(slotId, { query: 'never' });
    keepHeldSends('conv-1', queueOf(slot(slotId)));
    const other = streamingChat('conv-2');
    queueSend(other, { query: 'stays' });

    dropQueuedFor('conv-1');
    expect(queueOf(slot(slotId))).toEqual([]);
    expect(heldSendsOf('conv-1')).toEqual([]);
    expect(queries(other)).toEqual(['stays']);
  });
});

describe('the box', () => {
  it('takes an edited question back only while empty, and the slot knows when it holds text', () => {
    const slotId = streamingChat();
    const setText = vi.fn();
    const hook = renderHook(({ text }) => useQueuedSendComposer(slotId, text, setText), { initialProps: { text: '' } });
    act(() => useChatStore.getState().updateSlot(slotId, { composerRestore: 'edit me' }));
    expect(setText).toHaveBeenCalledWith('edit me');
    expect(slot(slotId).composerRestore).toBeNull();

    hook.rerender({ text: 'typing' });
    expect(slot(slotId).composerHasText).toBe(true);
    setText.mockClear();
    act(() => useChatStore.getState().updateSlot(slotId, { composerRestore: 'not over a draft' }));
    expect(setText).not.toHaveBeenCalled();
    expect(slot(slotId).composerRestore).toBeNull();
  });
});
