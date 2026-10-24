/**
 * A queued question the server refuses before its answer begins goes back to the head of the queue with the reason,
 * and the thread is left as it was; the rest wait on it. Only `fetch` is faked.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installMemoryStorage, jsonResponse, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';
import type { ThreadMessageLike } from '@assistant-ui/react';

installMemoryStorage();

// The default extension, read once loaded: importing it from the factory would load the chat modules against the real
// `@/config` instead of this one.
const config = vi.hoisted(() => ({ chatExtension: null as unknown, chatSlots: null as unknown }));
vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return {
    useAuthStore: auth.useAuthStore,
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
const { queueOf, queueSend } = await import('../utils/queued-send');
const { sendNextQueued } = await import('../utils/send-when-free');
const { streamMessageForSlot } = await import('../streaming');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();

const EARLIER: ThreadMessageLike[] = [
  { id: 'q0', role: 'user', content: [{ type: 'text', text: 'Earlier question' }] },
  { id: 'a0', role: 'assistant', content: [{ type: 'text', text: 'Earlier answer' }] },
];

/** A chat of yours with an answer in it, its next questions waiting. */
function chatWithQueue(...queries: string[]): string {
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(slotId, { isInitialized: true, isOwner: true, messages: EARLIER });
  useChatStore.setState({ activeSlotId: slotId });
  queries.forEach((query) => queueSend(slotId, { query }));
  return slotId;
}

const slot = (id: string) => useChatStore.getState().slots[id];
const texts = (id: string) => slot(id).messages.map((m) => getThreadMessagePlainText(m));
const settle = () => vi.waitFor(() => expect(slot(Object.keys(useChatStore.getState().slots)[0]).isStreaming).toBe(false));

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
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

describe('a queued question the server refuses', () => {
  it('goes back first in the queue with the reason, the thread as it was, and the rest wait on it', async () => {
    const slotId = chatWithQueue('Refused one', 'Behind it');
    fetchMock.mockResolvedValueOnce(jsonResponse(429, {}, { 'retry-after': '3' }));

    expect(sendNextQueued(slotId)).toBe(true);
    await settle();

    const [first, second] = queueOf(slot(slotId));
    expect(first).toMatchObject({ query: 'Refused one', failed: 'PipesHub is busy right now. Please try again in 3 seconds.' });
    expect(second).toMatchObject({ query: 'Behind it' });
    expect(texts(slotId)).toEqual(['Earlier question', 'Earlier answer']);
    expect(sendNextQueued(slotId)).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('a question typed and sent, not queued, still shows its error in the thread', async () => {
    const slotId = chatWithQueue();
    fetchMock.mockResolvedValueOnce(jsonResponse(429, {}, { 'retry-after': '3' }));

    await streamMessageForSlot(slotId, 'Typed now', { query: 'Typed now', conversationId: 'conv-1' } as never);
    await settle();

    expect(queueOf(slot(slotId))).toEqual([]);
    expect(texts(slotId)).toEqual([
      'Earlier question',
      'Earlier answer',
      'Typed now',
      'PipesHub is busy right now. Please try again in 3 seconds.',
    ]);
  });
});
