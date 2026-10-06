/**
 * A send that waits for another person's run (send-when-free) keeps its mentions all the way to the request.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

const streamed = vi.hoisted(() => ({ calls: [] as Array<{ slotId: string; query: string; request: Record<string, unknown> }> }));

vi.mock('@/config', () => ({ useAuthStore: { getState: () => ({}) }, logoutAndRedirect: vi.fn() }));
vi.mock('../streaming', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../streaming')>()),
  streamMessageForSlot: (slotId: string, query: string, request: Record<string, unknown>) => {
    streamed.calls.push({ slotId, query, request });
    return Promise.resolve();
  },
}));
vi.mock('../runtime', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../runtime')>()),
  buildStreamChatRequestForSlot: (_slotId: string, query: string) => ({ query }),
}));

const { useChatStore } = await import('../store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { buildExternalStoreConfig } = await import('../runtime');
const { sendQueuedMessage } = await import('../utils/send-when-free');
const { queueSend } = await import('../utils/queued-send');

const initialState = useChatStore.getState();
const BOB = { type: 'user' as const, id: 'u-bob' };
const ASSISTANT = { type: 'assistant' as const, id: 'self' };

function busySlot() {
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(slotId, {
    isInitialized: true,
    access: { role: 'write', isOwner: false, accessLevel: 'write', canSend: true, canManage: false, canInvite: false, isCollaborative: true },
    activeRun: { userId: 'u2', displayName: 'Bob', startedAt: '2026-10-01T00:00:00.000Z' },
  });
  useChatStore.setState({ activeSlotId: slotId });
  return slotId;
}

beforeEach(() => {
  streamed.calls.length = 0;
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: true } } as never);
});

describe('send-when-free keeps mentions', () => {
  it('queueSend stores them, and none when there are none', () => {
    const slotId = busySlot();
    queueSend(slotId, { query: 'hi <@user:u-bob>', mentions: [BOB, ASSISTANT] });
    expect(useChatStore.getState().slots[slotId].queuedSend?.mentions).toEqual([BOB, ASSISTANT]);
    queueSend(slotId, { query: 'plain' });
    expect(useChatStore.getState().slots[slotId].queuedSend).not.toHaveProperty('mentions');
  });

  it('a send made while someone else runs is queued with its mentions (runtime onNew)', async () => {
    const slotId = busySlot();
    const config = buildExternalStoreConfig(slotId);
    await config.onNew({
      role: 'user',
      content: [{ type: 'text', text: '@assistant what did <@user:u-bob> say?' }],
      metadata: { custom: { mentions: [ASSISTANT, BOB] } },
    } as never);
    expect(streamed.calls).toHaveLength(0);
    expect(useChatStore.getState().slots[slotId].queuedSend).toMatchObject({
      query: '@assistant what did <@user:u-bob> say?',
      mentions: [ASSISTANT, BOB],
    });
  });

  it('when the run ends the queued message goes out with the same mentions and client message id', () => {
    const slotId = busySlot();
    queueSend(slotId, { query: '@assistant recap', mentions: [ASSISTANT] });
    const queuedId = useChatStore.getState().slots[slotId].queuedSend?.clientMessageId;
    useChatStore.getState().updateSlot(slotId, { activeRun: null });
    expect(sendQueuedMessage(slotId)).toBe(true);
    expect(streamed.calls).toHaveLength(1);
    expect(streamed.calls[0].request).toMatchObject({ query: '@assistant recap', mentions: [ASSISTANT], clientMessageId: queuedId });
    expect(useChatStore.getState().slots[slotId].queuedSend).toBeNull();
  });

  it('a queued message without mentions sends none', () => {
    const slotId = busySlot();
    queueSend(slotId, { query: 'plain' });
    useChatStore.getState().updateSlot(slotId, { activeRun: null });
    sendQueuedMessage(slotId);
    expect(streamed.calls[0].request).not.toHaveProperty('mentions');
  });
});
