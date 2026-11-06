/**
 * The end of an answer moves the chat up with the time the server saved for it, so Pinned, which orders by time,
 * shows the server's order whatever this browser's clock says. Only `fetch` is faked.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn, sseFrame, sseResponse } from '@/lib/api/__tests__/sse-response';
import type { Conversation, StreamChatRequest } from '../types';

installMemoryStorage();

vi.mock('@/config', async (importOriginal) => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  const { chatExtension, chatSlots } = await importOriginal<typeof import('@/config')>();
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn(), chatExtension, chatSlots };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useChatStore } = await import('../store');
const { streamMessageForSlot } = await import('../streaming');
const { splitPinned } = await import('../utils/pinned-conversations');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();
/** The server's clock when the answer is saved. */
const T = new Date(2026, 9, 31, 12, 0, 0).getTime();
const MIN = 60 * 1000;

const pin = (id: string, lastActivityAt: number, extra: Partial<Conversation> = {}): Conversation => ({
  id,
  title: id,
  createdAt: '2026-10-01T00:00:00.000Z',
  updatedAt: '2026-10-01T00:00:00.000Z',
  isShared: false,
  sharedWith: [],
  lastActivityAt,
  isPinned: true,
  ...extra,
});

const timeOf = (id: string) =>
  [...useChatStore.getState().conversations, ...useChatStore.getState().sharedConversations].find((c) => c.id === id)?.lastActivityAt;

const pinned = () => {
  const s = useChatStore.getState();
  return splitPinned([...s.conversations, ...s.sharedConversations]).pinned.map((c) => c.id);
};

/**
 * A question in `id`, whose answer the server saved at `savedAt` and whose final frame it sent 10 ms later. A member of
 * someone else's shared chat is sent the chat without its `lastActivityAt`.
 */
async function answerEndsIn(id: string, savedAt: number, { member = false } = {}) {
  const slotId = useChatStore.getState().createSlot(id);
  useChatStore.setState({ activeSlotId: slotId });
  useChatStore.getState().updateSlot(slotId, { messages: [], rawMessages: [], hasLoaded: true });
  const conversation = { _id: id, title: id, status: 'Complete', messages: [], frame: 'turn', ...(!member && { lastActivityAt: savedAt }) };
  const done = sseFrame('RUN_FINISHED', {
    type: 'RUN_FINISHED',
    result: { conversation, meta: { requestId: 'r1', timestamp: new Date(savedAt + 10).toISOString(), duration: 900 } },
  });
  fetchMock.mockImplementationOnce(async (_url, init) => sseResponse({ chunks: [done], signal: init?.signal }));
  const request = { query: 'q', modelKey: 'm1', modelName: 'gpt-5', chatMode: 'quick', filters: { apps: [], kb: [] }, conversationId: id };
  await streamMessageForSlot(slotId, 'q', request as StreamChatRequest);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  // As the server lists them: two pins of yours and a pinned chat someone shared with you. a2 is about to get an answer.
  useChatStore.setState({
    ...initialState,
    slots: {},
    activeSlotId: null,
    conversations: [pin('a1', T - 10 * MIN), pin('a2', T - 20 * MIN)],
    sharedConversations: [pin('b1', T - 5 * MIN, { isShared: true, isOwner: false })],
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe.each([
  ['ahead of', 2 * MIN],
  ['behind', -2 * MIN],
])("with this browser's clock 2 min %s the server's", (_, skew) => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(T + skew);
  });

  it("your chat takes the time the server saved, so Pinned is in the server's order, and a chat active after it goes above it", async () => {
    await answerEndsIn('a2', T);

    expect(timeOf('a2')).toBe(T);
    expect(pinned()).toEqual(['a2', 'b1', 'a1']);

    // A chat whose answer the server saved 30 s later, pinned next (a new chat lists with the server's time).
    useChatStore.setState((s) => ({ conversations: [pin('cp', T + MIN / 2), ...s.conversations] }));
    expect(pinned()).toEqual(['cp', 'a2', 'b1', 'a1']);
  });

  it("a shared chat you asked in, sent without its time, takes the server's time on the frame", async () => {
    useChatStore.setState({ sharedConversations: [pin('b1', T - 30 * MIN, { isShared: true, isOwner: false })] });

    await answerEndsIn('b1', T, { member: true });

    expect(timeOf('b1')).toBe(T + 10);
    expect(pinned()).toEqual(['b1', 'a1', 'a2']);
    useChatStore.setState((s) => ({ conversations: [pin('cp', T + MIN / 2), ...s.conversations] }));
    expect(pinned()).toEqual(['cp', 'b1', 'a1', 'a2']);
  });
});
