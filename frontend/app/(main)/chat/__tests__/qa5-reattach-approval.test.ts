/**
 * QA5-F3 (found by EM-02's reload on the harness lane): a reattached durable run that waits on an approval shows
 * its card. The card is drawn on the row the message list treats as streaming, which needs `streamingQuestion`;
 * the reattach path never set it, so after a reload the run waited on a decision nobody could make. And a reload
 * that resumes after the frames this tab saw (a saved stream) never hears `approval_requested` again, so the
 * waiting card comes from the run's timeline.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';
import { RUN_ID, SESSION_ID, toWire, type DurableFrame } from './durable-frames';

installMemoryStorage('localStorage');
installMemoryStorage('sessionStorage');

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/config');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { useChatStore } = await import('../store');
const { resumeActiveDurableRunForSlot } = await import('../streaming');
const { buildMessagePairs } = await import('../components/message-area/message-pairs');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');

const AGENT = 'agent-1';
const QUESTION = 'Post the release note to #eng';
const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();

const heldRun: DurableFrame[] = [
  ['CUSTOM', { name: 'conversation_created', value: { conversationId: SESSION_ID, title: QUESTION } }],
  ['RUN_STARTED', { threadId: SESSION_ID, runId: RUN_ID }],
  ['TOOL_CALL_START', { toolCallId: 'call-1', toolCallName: 'slack__post_message', runId: RUN_ID }],
  [
    'CUSTOM',
    {
      name: 'approval_requested',
      value: { interactionId: 'ix-1', toolCallId: 'call-1', toolName: 'slack__post_message', args: '{}', argsSummary: null },
      runId: RUN_ID,
    },
  ],
].map(([type, fields], index) => ({
  id: `j:${index + 1}.1/0.0.0`,
  event: type as string,
  data: { type, ...(fields as Record<string, unknown>) },
}));

/** The run's frames so far, then the connection stays open: the run waits on the decision. */
function waitingStream(): void {
  fetchMock.mockImplementation(async (_url, init) => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(toWire(heldRun)));
        init?.signal?.addEventListener('abort', () => controller.error(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true });
      },
    });
    return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600) });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  useFeatureFlagsStore.setState({
    agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true },
  });
  vi.spyOn(console, 'info').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  for (const s of Object.values(useChatStore.getState().slots)) s.abortController?.abort();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('QA5-F3: a reloaded conversation whose run waits on an approval shows the card', () => {
  it('the row of the waiting turn is the live one, and it carries the pending approval', async () => {
    fakeApi({});
    waitingStream();
    const slotId = useChatStore.getState().createSlot(SESSION_ID);
    useChatStore.setState({ activeSlotId: slotId });
    useChatStore.getState().updateSlot(slotId, {
      messages: [{ role: 'user', content: [{ type: 'text', text: QUESTION }] }],
    });

    void resumeActiveDurableRunForSlot(slotId, AGENT, SESSION_ID, RUN_ID);
    await vi.waitFor(() =>
      expect(useChatStore.getState().slots[slotId].pendingApprovals?.map((a) => a.status)).toEqual(['pending']),
    );

    const slot = useChatStore.getState().slots[slotId];
    const pairs = buildMessagePairs(slot.messages, {
      isStreaming: slot.isStreaming,
      streamingQuestion: slot.streamingQuestion,
      pendingCollections: [],
      regenerateMessageId: null,
      emptyCitationMaps: { citations: {}, sources: [], citationsOrder: [] } as never,
    });
    expect(pairs.map((p) => [p.question, p.isStreaming])).toEqual([[QUESTION, true]]);
  });

  it('a reload that resumes after the frames already seen takes the waiting card from the run’s timeline', async () => {
    const approval = { interactionId: 'ix-1', toolCallId: 'call-1', toolName: 'slack__post_message', args: '{}', argsSummary: null };
    const timeline = `/api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}/timeline`;
    fakeApi({
      [`GET ${timeline}`]: { status: 200, data: { runId: RUN_ID, status: 'waiting', parts: [], approvals: [{ ...approval, status: 'pending' }], children: [] } },
    });
    waitingStream();
    const first = useChatStore.getState().createSlot(SESSION_ID);
    useChatStore.getState().updateSlot(first, { messages: [{ role: 'user', content: [{ type: 'text', text: QUESTION }] }] });
    void resumeActiveDurableRunForSlot(first, AGENT, SESSION_ID, RUN_ID);
    await vi.waitFor(() => expect(useChatStore.getState().slots[first].pendingApprovals).toHaveLength(1));
    useChatStore.getState().slots[first].abortController?.abort();

    // The reload: a fresh page state, and the stream answers only what comes after the saved frame (nothing yet).
    useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (_url, init) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          init?.signal?.addEventListener('abort', () => controller.error(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true });
        },
      });
      return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
    });
    const slotId = useChatStore.getState().createSlot(SESSION_ID);
    useChatStore.setState({ activeSlotId: slotId });
    useChatStore.getState().updateSlot(slotId, { messages: [{ role: 'user', content: [{ type: 'text', text: QUESTION }] }] });
    void resumeActiveDurableRunForSlot(slotId, AGENT, SESSION_ID, RUN_ID);

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect((fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string> | undefined)?.['Last-Event-ID']).toBe('j:4.1/0.0.0');
    await vi.waitFor(() =>
      expect(useChatStore.getState().slots[slotId].pendingApprovals?.map((a) => [a.interactionId, a.status])).toEqual([['ix-1', 'pending']]),
    );
  });
});
