import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStore } from '../../store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { CollaborationApi } from '../../collaboration-api';
import { FEED_NOT_MODIFIED, type AccessView, type FeedMessage, type FeedPage } from '../../collaboration-types';
import { useConversationSync } from '../use-conversation-sync';
import { loadDraft } from '../../utils/draft-storage';

type FlagsState = Partial<ReturnType<typeof useFeatureFlagsStore.getState>>;
const setFlag = (on: boolean) =>
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: on } } as FlagsState);

const access = (o: Partial<AccessView> = {}): AccessView => ({
  role: 'write', isOwner: false, accessLevel: 'write', canSend: true, canManage: false, canInvite: false,
  isCollaborative: true, ...o,
});

function feedMessage(seq: number, type: 'user_query' | 'bot_response', content: string): FeedMessage {
  return {
    _id: `m${seq}`, messageType: type, content, contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [],
    feedback: [], createdAt: '2026-10-01T00:00:00.000Z', updatedAt: '2026-10-01T00:00:00.000Z', seq,
    author: { userId: 'u2', displayName: 'Bob' },
  } as unknown as FeedMessage;
}

function page(rev: number, messages: FeedMessage[] = [], o: Partial<FeedPage> = {}): FeedPage {
  const last = messages.length ? messages[messages.length - 1].seq : 0;
  return { messages, rev, nextSeq: last, hasMore: false, activeRun: null, lastActivityAt: Date.now(), ...o };
}

function seed(extra: Record<string, unknown> = {}, acc: AccessView | null = access()) {
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(slotId, { access: acc, isInitialized: true, ...extra });
  useChatStore.setState({ activeSlotId: slotId });
  return slotId;
}

class FakeLocks {
  private held = new Set<string>();
  private waiting = new Map<string, Array<() => void>>();
  request = vi.fn(async (name: string, opts: { signal?: AbortSignal }, cb: () => Promise<void> | undefined) => {
    if (this.held.has(name)) {
      await new Promise<void>((resolve, reject) => {
        const q = this.waiting.get(name) ?? [];
        q.push(resolve);
        this.waiting.set(name, q);
        opts.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      });
    }
    this.held.add(name);
    try {
      await cb();
    } finally {
      this.held.delete(name);
      this.waiting.get(name)?.shift()?.();
    }
  });
}

class FakeChannel {
  static all = new Set<FakeChannel>();
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(public name: string) {
    FakeChannel.all.add(this);
  }
  postMessage(data: unknown) {
    for (const c of FakeChannel.all) {
      if (c !== this && c.name === this.name) c.onmessage?.({ data } as MessageEvent);
    }
  }
  addEventListener() {}
  removeEventListener() {}
  close() {
    FakeChannel.all.delete(this);
  }
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
  document.dispatchEvent(new Event('visibilitychange'));
}

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

let fetchFeed: ReturnType<typeof vi.spyOn>;
let hasFocus: ReturnType<typeof vi.spyOn>;
let locks: FakeLocks;

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(Math, 'random').mockReturnValue(0.5);
  useChatStore.getState().reset();
  setFlag(true);
  setVisibility('visible');
  window.localStorage.clear();
  locks = new FakeLocks();
  Object.defineProperty(navigator, 'locks', { configurable: true, value: locks });
  vi.stubGlobal('BroadcastChannel', FakeChannel);
  FakeChannel.all.clear();
  fetchFeed = vi.spyOn(CollaborationApi, 'fetchFeed');
  hasFocus = vi.spyOn(document, 'hasFocus').mockReturnValue(true);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, 'locks');
});

describe('polling budget across windows', () => {
  it('a visible window without focus polls at the idle cadence, not every 4 s', async () => {
    hasFocus.mockReturnValue(false);
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    await advance(11_000);
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    await advance(4_100);
    expect(fetchFeed).toHaveBeenCalledTimes(2);
  });

  it('regaining focus polls at the 4 s cadence again without waiting out the slow one', async () => {
    hasFocus.mockReturnValue(false);
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    await advance(2_000);
    hasFocus.mockReturnValue(true);
    act(() => { window.dispatchEvent(new Event('focus')); });
    await advance(4_100);
    expect(fetchFeed).toHaveBeenCalledTimes(2);
    await advance(4_000);
    expect(fetchFeed).toHaveBeenCalledTimes(3);
  });

  it('a focused window stays at about 15 requests a minute', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    await advance(60_000);
    const focusedCalls = fetchFeed.mock.calls.length - 1;
    expect(focusedCalls).toBeLessThanOrEqual(15);
    expect(focusedCalls).toBeGreaterThanOrEqual(14);
  });
});

describe('polling the feed', () => {
  it('fetches once on mount with the slot\'s rev and cursor, then every ~4 s', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    expect(fetchFeed.mock.calls[0][0]).toEqual({ kind: 'chat', id: 'conv-1' });
    expect(fetchFeed.mock.calls[0][1]).toEqual({ afterSeq: -1, rev: null });
    await advance(4000);
    expect(fetchFeed).toHaveBeenCalledTimes(2);
    await advance(4000);
    expect(fetchFeed).toHaveBeenCalledTimes(3);
  });

  it('uses the agent path for an agent chat', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed({ threadAgentId: 'agent-7' });
    renderHook(() => useConversationSync(slotId));
    await flush();
    expect(fetchFeed.mock.calls[0][0]).toEqual({ kind: 'agent', agentKey: 'agent-7', id: 'conv-1' });
  });

  it('writes nothing to the store on a 304', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed({ rev: 4 });
    const writes = vi.fn();
    renderHook(() => useConversationSync(slotId));
    const unsubscribe = useChatStore.subscribe(writes);
    await flush();
    await advance(12_000);
    expect(fetchFeed.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(writes).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('merges new messages, stores rev and activeRun, and sends the new rev next time', async () => {
    const run = { userId: 'u2', displayName: 'Bob', startedAt: '2026-10-01T00:00:00.000Z', runId: 'r1' };
    fetchFeed.mockResolvedValueOnce(page(3, [feedMessage(1, 'user_query', 'hi'), feedMessage(2, 'bot_response', 'hello')], { activeRun: run }));
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    const s = useChatStore.getState().slots[slotId];
    expect(s.messages.map((m) => m.id)).toEqual(['m1', 'm2']);
    expect(s.rev).toBe(3);
    expect(s.activeRun).toEqual(run);
    expect(s.messages[0].metadata?.custom).toMatchObject({ seq: 1, rev: 3, author: { userId: 'u2' } });
    await advance(4000);
    expect(fetchFeed.mock.calls[1][1]).toEqual({ afterSeq: 2, rev: 3 });
  });

  it('writes nothing when the same page arrives again', async () => {
    const same = page(3, [feedMessage(1, 'user_query', 'hi')]);
    fetchFeed.mockResolvedValue(same);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    const writes = vi.fn();
    const unsubscribe = useChatStore.subscribe(writes);
    await advance(4000);
    expect(fetchFeed).toHaveBeenCalledTimes(2);
    expect(writes).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('keeps fetching at once while the server says there is more', async () => {
    fetchFeed
      .mockResolvedValueOnce(page(2, [feedMessage(1, 'user_query', 'a')], { hasMore: true }))
      .mockResolvedValueOnce(page(2, [feedMessage(2, 'bot_response', 'b')]))
      .mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(2);
    expect(fetchFeed.mock.calls[1][1]).toMatchObject({ afterSeq: 1 });
    expect(useChatStore.getState().slots[slotId].messages).toHaveLength(2);
  });

  it('slows down with the conversation\'s idleness', async () => {
    const old = Date.now() - 10 * 60_000;
    fetchFeed.mockResolvedValue(page(1, [], { lastActivityAt: old }));
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    await advance(14_000);
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    await advance(1_500);
    expect(fetchFeed).toHaveBeenCalledTimes(2);
  });
});

describe('when it does not poll', () => {
  it.each([
    ['the flag is off', () => { setFlag(false); return seed(); }],
    ['the chat is solo', () => seed({}, access({ isCollaborative: false }))],
    ['access is not known yet', () => seed({}, null)],
    ['the slot is a new, unsaved chat', () => seed({ isTemp: true })],
    ['the user\'s own stream is running', () => seed({ isStreaming: true })],
    ['access was lost', () => seed({ accessLost: true })],
  ])('when %s', async (_name, setup) => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = setup();
    renderHook(() => useConversationSync(slotId));
    await flush();
    await advance(60_000);
    expect(fetchFeed).not.toHaveBeenCalled();
  });

  it('when the slot is not the active one', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    const other = useChatStore.getState().createSlot('conv-2');
    useChatStore.setState({ activeSlotId: other });
    renderHook(() => useConversationSync(slotId));
    await flush();
    await advance(10_000);
    expect(fetchFeed).not.toHaveBeenCalled();
  });

  it('pauses while the stream runs and resumes after it', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    act(() => useChatStore.getState().updateSlot(slotId, { isStreaming: true }));
    await advance(30_000);
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    act(() => useChatStore.getState().updateSlot(slotId, { isStreaming: false }));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(2);
  });

  it('pauses in a hidden tab and catches up when it shows again', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    act(() => setVisibility('hidden'));
    await advance(60_000);
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    act(() => setVisibility('visible'));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(2);
  });

  it('stops polling when the component unmounts', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    const { unmount } = renderHook(() => useConversationSync(slotId));
    await flush();
    unmount();
    await advance(30_000);
    expect(fetchFeed).toHaveBeenCalledTimes(1);
  });
});

describe('failures', () => {
  it('backs off exponentially after errors and recovers after a success', async () => {
    fetchFeed.mockRejectedValueOnce({ statusCode: 503 }).mockRejectedValueOnce({ statusCode: 503 }).mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    const { result } = renderHook(() => useConversationSync(slotId));
    await flush();
    expect(result.current.error).toEqual({ statusCode: 503 });
    await advance(7_900);
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    await advance(200);
    expect(fetchFeed).toHaveBeenCalledTimes(2);
    await advance(15_800);
    expect(fetchFeed).toHaveBeenCalledTimes(2);
    await advance(200);
    expect(fetchFeed).toHaveBeenCalledTimes(3);
    expect(result.current.error).toBeNull();
    await advance(4_000);
    expect(fetchFeed).toHaveBeenCalledTimes(4);
    expect(useChatStore.getState().slots[slotId].accessLost).toBe(false);
  });

  it('waits at least the server\'s retryAfter after a 429, without marking access lost', async () => {
    fetchFeed
      .mockRejectedValueOnce({ statusCode: 429, code: 'RATE_LIMITED', details: { retryAfter: 30 } })
      .mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    await advance(29_000);
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    await advance(1_500);
    expect(fetchFeed).toHaveBeenCalledTimes(2);
    expect(useChatStore.getState().slots[slotId].accessLost).toBe(false);
  });
});

describe('access lost (D-b, CL-27)', () => {
  it.each([404, 403])('a %i marks the slot, keeps history and the draft, and stops polling', async (status) => {
    fetchFeed.mockRejectedValue({ statusCode: status });
    const slotId = seed({
      messages: [{ id: 'm1', role: 'user', content: [{ type: 'text', text: 'kept' }] }],
      queuedSend: { query: 'unsent words', clientMessageId: 'cm', queuedAt: 1 },
    });
    renderHook(() => useConversationSync(slotId));
    await flush();
    const s = useChatStore.getState().slots[slotId];
    expect(s.accessLost).toBe(true);
    expect(s.messages).toHaveLength(1);
    expect(s.queuedSend).toBeNull();
    expect(loadDraft('conv-1')).toBe('unsent words');
    expect(useChatStore.getState().slots[slotId]).toBeDefined();
    await advance(120_000);
    expect(fetchFeed).toHaveBeenCalledTimes(1);
  });

  it('keeps the slot while it is active and evicts it, and its sidebar rows, on deactivation', async () => {
    fetchFeed.mockRejectedValue({ statusCode: 404 });
    const slotId = seed();
    useChatStore.setState({
      conversations: [{ id: 'conv-1', title: 't' } as never],
      sharedConversations: [{ id: 'conv-1', title: 't' } as never],
    });
    const { rerender } = renderHook(({ id }) => useConversationSync(id), { initialProps: { id: slotId as string | null } });
    await flush();
    expect(useChatStore.getState().slots[slotId].accessLost).toBe(true);
    expect(useChatStore.getState().conversations).toHaveLength(1);

    rerender({ id: null });
    expect(useChatStore.getState().slots[slotId]).toBeUndefined();
    expect(useChatStore.getState().conversations).toHaveLength(0);
    expect(useChatStore.getState().sharedConversations).toHaveLength(0);
  });

  it('evicts a lost slot when the page unmounts', async () => {
    fetchFeed.mockRejectedValue({ statusCode: 403 });
    const slotId = seed();
    const { unmount } = renderHook(() => useConversationSync(slotId));
    await flush();
    unmount();
    expect(useChatStore.getState().slots[slotId]).toBeUndefined();
  });

  it('does not evict a healthy slot on deactivation', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    const { unmount } = renderHook(() => useConversationSync(slotId));
    await flush();
    unmount();
    expect(useChatStore.getState().slots[slotId]).toBeDefined();
  });

  it('never hands a lost slot out again from the cache', () => {
    const slotId = seed({ accessLost: true });
    expect(useChatStore.getState().getSlotByConvId('conv-1')).toBeNull();
    useChatStore.getState().updateSlot(slotId, { accessLost: false });
    expect(useChatStore.getState().getSlotByConvId('conv-1')?.slotId).toBe(slotId);
  });
});

describe('one poller per conversation across tabs', () => {
  it('makes one request per tick with two hook instances and shares the result', async () => {
    fetchFeed.mockResolvedValue(page(2, [feedMessage(1, 'user_query', 'shared')]));
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    renderHook(() => useConversationSync(slotId));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    // The follower fetches once when it joins (to fill what it missed), then only the leader polls.
    await advance(1000);
    expect(fetchFeed).toHaveBeenCalledTimes(2);
    await advance(3000);
    expect(fetchFeed).toHaveBeenCalledTimes(3);
    await advance(4000);
    expect(fetchFeed).toHaveBeenCalledTimes(4);
    expect(locks.request).toHaveBeenCalledTimes(2);
  });

  it('fetches the gap when another tab\'s page starts past what this tab has', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    fetchFeed.mockClear();
    fetchFeed.mockResolvedValue(page(6, [feedMessage(1, 'user_query', 'missed'), feedMessage(2, 'bot_response', 'gap'), feedMessage(3, 'user_query', 'new')]));
    const remote = new FakeChannel('ph-conv-sync:conv-1');
    act(() => remote.postMessage({ type: 'feed', page: page(6, [feedMessage(3, 'user_query', 'new')]), afterSeq: 2 }));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(1);
    expect(fetchFeed.mock.calls[0][1]).toEqual({ afterSeq: -1, rev: null });
    expect(useChatStore.getState().slots[slotId].messages).toHaveLength(3);
  });

  it('applies a page another tab fetched, and its access-lost news', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    const remote = new FakeChannel('ph-conv-sync:conv-1');
    act(() => remote.postMessage({ type: 'feed', page: page(5, [feedMessage(1, 'user_query', 'from another tab')]) }));
    expect(useChatStore.getState().slots[slotId].messages).toHaveLength(1);
    expect(useChatStore.getState().slots[slotId].rev).toBe(5);
    act(() => remote.postMessage({ type: 'lost' }));
    expect(useChatStore.getState().slots[slotId].accessLost).toBe(true);
  });

  it('ignores a page older than the slot\'s rev', async () => {
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed({ rev: 9 });
    renderHook(() => useConversationSync(slotId));
    await flush();
    const remote = new FakeChannel('ph-conv-sync:conv-1');
    act(() => remote.postMessage({ type: 'feed', page: page(4, [feedMessage(1, 'user_query', 'old')]) }));
    expect(useChatStore.getState().slots[slotId].messages).toHaveLength(0);
  });

  it('elects through a BroadcastChannel heartbeat when there are no locks', async () => {
    Reflect.deleteProperty(navigator, 'locks');
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await advance(700);
    expect(fetchFeed).toHaveBeenCalledTimes(1);
  });

  it('polls on its own when neither locks nor BroadcastChannel exist', async () => {
    Reflect.deleteProperty(navigator, 'locks');
    vi.stubGlobal('BroadcastChannel', undefined);
    fetchFeed.mockResolvedValue(FEED_NOT_MODIFIED);
    const slotId = seed();
    renderHook(() => useConversationSync(slotId));
    await flush();
    expect(fetchFeed).toHaveBeenCalledTimes(1);
  });
});
