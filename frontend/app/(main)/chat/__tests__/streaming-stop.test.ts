/**
 * Unit tests for `cancelStreamForSlot`'s graceful-stop flow (Stop Generation
 * plan, Phase 4): cooperative cancel-first, `stopping` gating, and the
 * grace-timeout hard-abort fallback that commits a locally-synthesized
 * `status: 'stopped'` message when the backend doesn't finish in time.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { ThreadMessageLike } from '@assistant-ui/react';
import type { useChatStore as UseChatStoreType } from '../store';
import type { ChatApi as ChatApiType } from '../api';
import type { ChatSlot } from '../types';

// Node 22's experimental global `localStorage` (see the `--localstorage-file`
// warning under vitest) shadows jsdom's own implementation and throws on
// first access in this environment — which crashes `auth-store.ts`'s
// import-time `hydrateAuthStore()` call, pulled in transitively via
// `@/lib/api` from `../store`/`../streaming`. Stub a working Storage before
// dynamically importing any app module below so that side effect is benign.
// (This is an environment quirk, not something Stop Generation introduces —
// the same crash pre-exists for e.g. `lib/store/__tests__/auth-store.test.ts`
// run in isolation.)
if (typeof window !== 'undefined') {
  const backing = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => (backing.has(key) ? (backing.get(key) as string) : null),
      setItem: (key: string, value: string) => backing.set(key, String(value)),
      removeItem: (key: string) => backing.delete(key),
      clear: () => backing.clear(),
      key: (index: number) => Array.from(backing.keys())[index] ?? null,
      get length() {
        return backing.size;
      },
    },
  });
}

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
}));

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...actual,
    ChatApi: {
      ...actual.ChatApi,
      cancelStream: vi.fn(),
    },
  };
});

// Dynamic imports (not static top-level `import`s) so the localStorage stub
// above runs first — `../store`/`../streaming`'s module graph is what
// triggers the crashing import-time hydration described above.
const { useChatStore } = (await import('../store')) as unknown as { useChatStore: typeof UseChatStoreType };
const { ChatApi } = (await import('../api')) as unknown as { ChatApi: typeof ChatApiType };
const { cancelStreamForSlot } = await import('../streaming');

const SLOT_ID = 'slot-under-test';

/** Minimal ChatSlot builder — only the fields `cancelStreamForSlot` reads/writes matter. */
function makeSlot(overrides: Partial<ChatSlot> = {}): ChatSlot {
  return {
    convId: 'conv-1',
    threadAgentId: null,
    agentStreamTools: null,
    isTemp: false,
    isInitialized: true,
    hasLoaded: true,
    messages: [],
    isStreaming: true,
    streamingContent: '',
    streamingQuestion: 'What is the plan?',
    currentStatusMessage: null,
    streamingCitationMaps: null,
    streamingParts: [],
    userScrollOverride: false,
    savedScrollTop: null,
    savedScrollWasStreaming: false,
    activeExpandedMessageId: null,
    regenerateMessageId: null,
    pendingCollections: [],
    artifacts: [],
    pendingAskUserQuestion: null,
    abortController: new AbortController(),
    runId: 'run-123',
    stopping: false,
    messagePagination: null,
    lastAccessedAt: Date.now(),
    isOwner: true,
    ...overrides,
  };
}

function seedSlot(overrides: Partial<ChatSlot> = {}): ChatSlot {
  const slot = makeSlot(overrides);
  useChatStore.setState({ slots: { [SLOT_ID]: slot } });
  return slot;
}

const mockedCancelStream = vi.mocked(ChatApi.cancelStream);

beforeEach(() => {
  mockedCancelStream.mockReset();
  mockedCancelStream.mockResolvedValue({ cancelled: true });
  useChatStore.setState({ slots: {} });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('cancelStreamForSlot — guards', () => {
  it('no-ops when the slot does not exist', () => {
    cancelStreamForSlot('missing-slot');
    expect(mockedCancelStream).not.toHaveBeenCalled();
  });

  it('no-ops when the slot is not streaming', () => {
    seedSlot({ isStreaming: false });
    cancelStreamForSlot(SLOT_ID);
    expect(mockedCancelStream).not.toHaveBeenCalled();
  });

  it('no-ops on a second call while already stopping (second click)', () => {
    seedSlot({ stopping: true });
    cancelStreamForSlot(SLOT_ID);
    expect(mockedCancelStream).not.toHaveBeenCalled();
  });
});

describe('cancelStreamForSlot — no convId/runId (very first turn)', () => {
  it('hard-aborts immediately when convId is not yet known', () => {
    const abortController = new AbortController();
    const abortSpy = vi.spyOn(abortController, 'abort');
    seedSlot({ convId: null, abortController });

    cancelStreamForSlot(SLOT_ID);

    expect(abortSpy).toHaveBeenCalledTimes(1);
    expect(mockedCancelStream).not.toHaveBeenCalled();
    const slot = useChatStore.getState().slots[SLOT_ID];
    expect(slot?.isStreaming).toBe(false);
    expect(slot?.stopping).toBe(false);
    expect(slot?.runId).toBeNull();
  });

  it('hard-aborts immediately when runId is not yet known', () => {
    const abortController = new AbortController();
    const abortSpy = vi.spyOn(abortController, 'abort');
    seedSlot({ runId: null, abortController });

    cancelStreamForSlot(SLOT_ID);

    expect(abortSpy).toHaveBeenCalledTimes(1);
    expect(mockedCancelStream).not.toHaveBeenCalled();
  });

  it('clears the pending sidebar entry for a still-temp slot on the no-run-id fallback', () => {
    seedSlot({ convId: null, isTemp: true });
    const clearSpy = vi.spyOn(useChatStore.getState(), 'clearPendingConversation');

    cancelStreamForSlot(SLOT_ID);

    expect(clearSpy).toHaveBeenCalledWith(SLOT_ID);
  });
});

describe('cancelStreamForSlot — cooperative cancel path', () => {
  it('marks the slot stopping and posts cancel WITHOUT aborting the connection', () => {
    const abortController = new AbortController();
    const abortSpy = vi.spyOn(abortController, 'abort');
    seedSlot({ abortController, convId: 'conv-1', runId: 'run-123', threadAgentId: null });

    cancelStreamForSlot(SLOT_ID);

    expect(useChatStore.getState().slots[SLOT_ID]?.stopping).toBe(true);
    expect(abortSpy).not.toHaveBeenCalled();
    expect(mockedCancelStream).toHaveBeenCalledWith('conv-1', 'run-123', undefined);
  });

  it('forwards the agent id for a scoped agent conversation', () => {
    seedSlot({ threadAgentId: 'agent-42' });

    cancelStreamForSlot(SLOT_ID);

    expect(mockedCancelStream).toHaveBeenCalledWith('conv-1', 'run-123', 'agent-42');
  });

  it('does not throw and still arms the grace timer when the cancel POST rejects', async () => {
    mockedCancelStream.mockRejectedValue(new Error('network down'));
    const abortController = new AbortController();
    const abortSpy = vi.spyOn(abortController, 'abort');
    seedSlot({ abortController });

    cancelStreamForSlot(SLOT_ID);
    // Let the rejected promise's `.catch` microtask settle before advancing timers.
    await vi.advanceTimersByTimeAsync(0);

    vi.advanceTimersByTime(5000);
    await vi.advanceTimersByTimeAsync(0);

    expect(abortSpy).toHaveBeenCalledTimes(1);
  });
});

describe('cancelStreamForSlot — grace-timeout fallback', () => {
  it('hard-aborts and commits streamingContent as a stopped message when the run has not settled', () => {
    const abortController = new AbortController();
    const abortSpy = vi.spyOn(abortController, 'abort');
    const placeholder: ThreadMessageLike = {
      role: 'assistant',
      id: 'asst-1',
      content: [{ type: 'text', text: '' }],
    };
    seedSlot({
      abortController,
      streamingContent: 'Here is the partial answer',
      messages: [
        { role: 'user', id: 'user-1', content: [{ type: 'text', text: 'What is the plan?' }] },
        placeholder,
      ],
    });

    cancelStreamForSlot(SLOT_ID);
    vi.advanceTimersByTime(5000);

    expect(abortSpy).toHaveBeenCalledTimes(1);
    const slot = useChatStore.getState().slots[SLOT_ID];
    expect(slot?.isStreaming).toBe(false);
    expect(slot?.stopping).toBe(false);
    expect(slot?.runId).toBeNull();
    expect(slot?.messages).toHaveLength(2);
    const lastMessage = slot?.messages[1] as ThreadMessageLike;
    expect(lastMessage.id).toBe('asst-1');
    expect(lastMessage.content).toEqual([{ type: 'text', text: 'Here is the partial answer' }]);
    expect((lastMessage.metadata?.custom as { status?: string } | undefined)?.status).toBe('stopped');
  });

  it('drops the empty placeholder assistant row when nothing streamed yet (stopped during Thinking)', () => {
    const placeholder: ThreadMessageLike = {
      role: 'assistant',
      id: 'asst-1',
      content: [{ type: 'text', text: '' }],
    };
    seedSlot({
      streamingContent: '',
      messages: [
        { role: 'user', id: 'user-1', content: [{ type: 'text', text: 'What is the plan?' }] },
        placeholder,
      ],
    });

    cancelStreamForSlot(SLOT_ID);
    vi.advanceTimersByTime(5000);

    const slot = useChatStore.getState().slots[SLOT_ID];
    expect(slot?.messages).toHaveLength(1);
    expect(slot?.messages[0].id).toBe('user-1');
  });

  it('replaces the target message in place for a regenerate run, leaving other messages untouched', () => {
    const target: ThreadMessageLike = {
      role: 'assistant',
      id: 'asst-target',
      content: [{ type: 'text', text: 'old answer' }],
    };
    const other: ThreadMessageLike = {
      role: 'assistant',
      id: 'asst-other',
      content: [{ type: 'text', text: 'unrelated' }],
    };
    seedSlot({
      regenerateMessageId: 'asst-target',
      streamingContent: 'new partial answer',
      messages: [other, target],
    });

    cancelStreamForSlot(SLOT_ID);
    vi.advanceTimersByTime(5000);

    const slot = useChatStore.getState().slots[SLOT_ID];
    expect(slot?.regenerateMessageId).toBeNull();
    const updated = slot?.messages.find((m) => m.id === 'asst-target');
    const untouched = slot?.messages.find((m) => m.id === 'asst-other');
    expect(updated?.content).toEqual([{ type: 'text', text: 'new partial answer' }]);
    expect((updated?.metadata?.custom as { status?: string } | undefined)?.status).toBe('stopped');
    expect(untouched?.content).toEqual([{ type: 'text', text: 'unrelated' }]);
  });

  it('leaves the pre-regenerate answer untouched when nothing new streamed yet', () => {
    const target: ThreadMessageLike = {
      role: 'assistant',
      id: 'asst-target',
      content: [{ type: 'text', text: 'old answer' }],
    };
    seedSlot({ regenerateMessageId: 'asst-target', streamingContent: '', messages: [target] });

    cancelStreamForSlot(SLOT_ID);
    vi.advanceTimersByTime(5000);

    const slot = useChatStore.getState().slots[SLOT_ID];
    expect(slot?.messages[0].content).toEqual([{ type: 'text', text: 'old answer' }]);
  });

  it('does nothing if RUN_FINISHED already settled the run before the grace timer fires', () => {
    const abortController = new AbortController();
    const abortSpy = vi.spyOn(abortController, 'abort');
    seedSlot({ abortController });

    cancelStreamForSlot(SLOT_ID);
    // Simulate the normal completion path (onComplete) landing before the
    // grace timer elapses — same field reset `streamMessageForSlot`'s
    // onComplete performs.
    useChatStore.getState().updateSlot(SLOT_ID, {
      isStreaming: false,
      runId: null,
      stopping: false,
      abortController: null,
    });

    vi.advanceTimersByTime(5000);

    // The grace timer must not re-abort an already-null controller or
    // otherwise touch a slot that already settled normally.
    expect(abortSpy).not.toHaveBeenCalled();
  });

  it('does nothing if a brand-new stream (different runId) started on this slot before the timer fires', () => {
    const originalAbort = new AbortController();
    const originalAbortSpy = vi.spyOn(originalAbort, 'abort');
    seedSlot({ abortController: originalAbort, runId: 'run-123' });

    cancelStreamForSlot(SLOT_ID);

    const newAbort = new AbortController();
    const newAbortSpy = vi.spyOn(newAbort, 'abort');
    useChatStore.getState().updateSlot(SLOT_ID, {
      runId: 'run-456',
      abortController: newAbort,
      isStreaming: true,
    });

    vi.advanceTimersByTime(5000);

    expect(originalAbortSpy).not.toHaveBeenCalled();
    expect(newAbortSpy).not.toHaveBeenCalled();
  });
});
