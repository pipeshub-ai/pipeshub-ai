/**
 * Durable agent chat in the slot (S7): reattaching a conversation's in-flight
 * run and stopping one. `apiClient` is faked at the axios adapter and the run
 * stream at `fetch`, so the whole path from the slot to the wire runs.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn, sseResponse } from '@/lib/api/__tests__/sse-response';
import {
  RUN_ID,
  SESSION_ID,
  ANSWER,
  asOwner,
  cancelledRun,
  completedRun,
  toWire,
  type DurableFrame,
} from './durable-frames';

installMemoryStorage('localStorage');
installMemoryStorage('sessionStorage');

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/config');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { useChatStore } = await import('../store');
const { resumeActiveDurableRunForSlot, streamMessageForSlot, streamRegenerateForSlot, cancelStreamForSlot } = await import('../streaming');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { getThreadMessagePlainText } = await import('../runtime');

const AGENT = 'agent-1';
const STREAM_URL = `/api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}/stream`;
const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();

function newSlot(): string {
  const slotId = useChatStore.getState().createSlot(SESSION_ID);
  useChatStore.setState({ activeSlotId: slotId });
  useChatStore.getState().updateSlot(slotId, {
    messages: [{ role: 'user', content: [{ type: 'text', text: 'Question' }] }],
  });
  return slotId;
}

const slot = (id: string) => useChatStore.getState().slots[id];
const texts = (id: string) => slot(id).messages.map((m) => [m.role, getThreadMessagePlainText(m)]);
const streamRequests = () => fetchMock.mock.calls.map((call) => String(call[0]));

/**
 * A run stream that sends frames only when the test releases them, like a
 * run in progress. The fetch signal is kept to prove Stop leaves it open.
 */
function gatedStream(): { release: (frames: DurableFrame[]) => void; signal: () => AbortSignal | null } {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let signal: AbortSignal | null = null;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  fetchMock.mockImplementationOnce(async (_url, init) => {
    signal = init?.signal ?? null;
    signal?.addEventListener('abort', () => controller.error(Object.assign(new Error('aborted'), { name: 'AbortError' })), {
      once: true,
    });
    return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
  });
  return {
    release: (frames) => {
      controller.enqueue(new TextEncoder().encode(toWire(frames)));
      if (frames.some((f) => f.event === 'RUN_FINISHED' || f.event === 'RUN_ERROR')) controller.close();
    },
    signal: () => signal,
  };
}

function harness(on: boolean): void {
  useFeatureFlagsStore.setState({
    agentHarness: on ? { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true } : null,
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600) });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  harness(true);
  vi.spyOn(console, 'info').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('reattaching the in-flight run (F-13, F-1)', () => {
  it('follows the run named by the conversation GET and settles on its answer', async () => {
    const api = fakeApi({});
    fetchMock.mockImplementation(async (_url, init) =>
      sseResponse({ chunks: [toWire(completedRun())], signal: init?.signal }),
    );
    const slotId = newSlot();

    await resumeActiveDurableRunForSlot(slotId, AGENT, SESSION_ID, RUN_ID);

    expect(streamRequests()).toEqual([STREAM_URL]);
    expect(api.sent.map((r) => `${r.method} ${r.url}`)).toEqual([]);
    expect(slot(slotId).isStreaming).toBe(false);
    expect(texts(slotId)).toEqual([
      ['user', 'Question'],
      ['assistant', ANSWER],
    ]);
  });

  it('announces a reattached run that finishes while another chat is open, as a fresh send does (#4249)', async () => {
    fakeApi({});
    fetchMock.mockImplementation(async (_url, init) =>
      sseResponse({ chunks: [toWire(completedRun())], signal: init?.signal }),
    );
    const slotId = newSlot();
    useChatStore.setState({ activeSlotId: null });

    await resumeActiveDurableRunForSlot(slotId, AGENT, SESSION_ID, RUN_ID);

    expect(useChatStore.getState().unreadConversationIds.has(SESSION_ID)).toBe(true);
  });

  it('asks nothing for a conversation with no run in flight', async () => {
    const api = fakeApi({});
    const slotId = newSlot();
    await resumeActiveDurableRunForSlot(slotId, AGENT, SESSION_ID, null);
    await resumeActiveDurableRunForSlot(slotId, AGENT, SESSION_ID, undefined);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(api.sent).toEqual([]);
    expect(slot(slotId).isStreaming).toBe(false);
  });

  it('does not reattach when the rollout has no harness (F-4)', async () => {
    harness(false);
    const api = fakeApi({});
    const slotId = newSlot();
    await resumeActiveDurableRunForSlot(slotId, AGENT, SESSION_ID, RUN_ID);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(api.sent).toEqual([]);
  });
});

describe('Stop on a durable run (F-3)', () => {
  const upToPartial = (frames: DurableFrame[]) =>
    frames.slice(0, frames.findIndex((f) => (f.data as { delta?: string }).delta === ' is 4') + 1);

  it('issues a durable cancel, keeps listening, and shows the stopped partial answer the run saved', async () => {
    const api = fakeApi({
      [`GET /api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}`]: {
        status: 200,
        data: { run: { runId: RUN_ID, status: 'running', revision: 7 } },
      },
      [`POST /api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}/cancel`]: {
        status: 200,
        data: { receipt: { commandId: 'c1' } },
      },
    });
    const stream = gatedStream();
    const slotId = useChatStore.getState().createSlot(SESSION_ID);
    useChatStore.setState({ activeSlotId: slotId });

    const run = streamMessageForSlot(slotId, 'Question', {
      query: 'Question',
      conversationId: SESSION_ID,
      agentId: AGENT,
      modelKey: 'm1',
      modelName: 'gpt-5',
      modelFriendlyName: 'GPT-5',
      chatMode: 'agent:auto',
      filters: { apps: [], kb: [] },
    });
    // One request on the chat route (LAT1): its first frame names the run.
    stream.release(upToPartial(asOwner(cancelledRun())));
    await vi.waitFor(() => expect(slot(slotId).durableRun?.runId).toBe(RUN_ID));
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toBe('The answer is 4'));

    cancelStreamForSlot(slotId);

    expect(slot(slotId).stopping).toBe(true);
    expect(slot(slotId).isStreaming).toBe(true);
    await vi.waitFor(() =>
      expect(api.sent.find((r) => r.url.endsWith('/cancel'))?.body).toEqual({ idempotencyKey: `stop-${RUN_ID}` }),
    );
    expect(stream.signal()?.aborted).toBe(false);

    stream.release(asOwner(cancelledRun()).slice(upToPartial(cancelledRun()).length));
    await run;

    const settled = slot(slotId);
    expect(settled.isStreaming).toBe(false);
    expect(settled.stopping).toBe(false);
    expect(settled.durableRun).toBeNull();
    const last = settled.messages[settled.messages.length - 1];
    expect(getThreadMessagePlainText(last)).toBe('The answer is 4');
    expect((last.metadata?.custom as { status?: string } | undefined)?.status).toBe('stopped');
  });

  it('commits the partial answer as stopped only when the run does not settle within the durable grace period', async () => {
    fakeApi({
      [`GET /api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}`]: {
        status: 200,
        data: { run: { runId: RUN_ID, status: 'running', revision: 7 } },
      },
      [`POST /api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}/cancel`]: { status: 503 },
    });
    const stream = gatedStream();
    const slotId = newSlot();
    const run = resumeActiveDurableRunForSlot(slotId, AGENT, SESSION_ID, RUN_ID);
    stream.release(upToPartial(cancelledRun()));
    await vi.waitFor(() => expect(slot(slotId).streamingContent).toBe('The answer is 4'));

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      cancelStreamForSlot(slotId);
      expect(slot(slotId).stopping).toBe(true);
      // UX3 ST-7: the server stops promptly and saves the streamed text, so the chat waits for that answer.
      await vi.advanceTimersByTimeAsync(5_000);
      expect(slot(slotId).isStreaming).toBe(true);
      await vi.advanceTimersByTimeAsync(15_000);
    } finally {
      vi.useRealTimers();
    }
    await run;

    const settled = slot(slotId);
    expect(stream.signal()?.aborted).toBe(true);
    expect(settled.isStreaming).toBe(false);
    expect(settled.durableRun).toBeNull();
    const last = settled.messages[settled.messages.length - 1];
    expect(getThreadMessagePlainText(last)).toBe('The answer is 4');
    expect((last.metadata?.custom as { status?: string } | undefined)?.status).toBe('stopped');
  });
});

describe('LAT1 FE-5: Stop before the turn’s first frame', () => {
  const turn = {
    query: 'Question',
    conversationId: SESSION_ID,
    agentId: AGENT,
    modelKey: 'm1',
    modelName: 'gpt-5',
    modelFriendlyName: 'GPT-5',
    chatMode: 'agent:auto' as const,
    filters: { apps: [], kb: [] },
  };

  it('waits for the first frame, then cancels the durable run it names; the stopped answer is the server’s', async () => {
    const api = fakeApi({
      [`POST /api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}/cancel`]: {
        status: 200,
        data: { receipt: { commandId: 'c1' } },
      },
    });
    const stream = gatedStream();
    const slotId = newSlot();
    const run = streamMessageForSlot(slotId, 'Question', { ...turn });
    await vi.waitFor(() => expect(slot(slotId).routePending).toBe(true));

    cancelStreamForSlot(slotId);
    expect(slot(slotId).stopping).toBe(true);
    expect(stream.signal()?.aborted, 'the request is kept: a run may already be admitted').toBe(false);
    expect(api.sent.filter((r) => r.url.endsWith('/cancel'))).toEqual([]);

    const frames = asOwner(cancelledRun());
    stream.release(frames.slice(0, 1));
    await vi.waitFor(() =>
      expect(api.sent.map((r) => r.url)).toContain(`/api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}/cancel`),
    );
    expect(api.sent.map((r) => r.url), 'never today’s cancel for a durable turn').not.toContain(
      `/api/v1/agents/${AGENT}/conversations/${SESSION_ID}/cancel`,
    );
    stream.release(frames.slice(1));
    await run;
    const settled = slot(slotId);
    expect(settled.isStreaming).toBe(false);
    expect((settled.messages[settled.messages.length - 1].metadata?.custom as { status?: string } | undefined)?.status).toBe(
      'stopped',
    );
    expect(streamRequests(), 'one request: nothing reattached').toEqual([
      expect.stringContaining(`/api/v1/agents/${AGENT}/conversations/${SESSION_ID}/messages/stream`),
    ]);
  });

  it('when today’s loop answers, stops it with the legacy cancel of this turn’s run id', async () => {
    const api = fakeApi({
      [`POST /api/v1/agents/${AGENT}/conversations/${SESSION_ID}/cancel`]: { status: 200, data: { cancelled: true } },
    });
    const stream = gatedStream();
    const slotId = newSlot();
    const run = streamMessageForSlot(slotId, 'Question', { ...turn, chatMode: 'agent:planExecute' });
    await vi.waitFor(() => expect(slot(slotId).routePending).toBe(true));
    const runId = slot(slotId).runId;

    cancelStreamForSlot(slotId);
    stream.release(cancelledRun().slice(0, 1));
    await vi.waitFor(() => expect(api.sent.find((r) => r.url.endsWith('/cancel'))?.body).toEqual({ runId }));
    stream.release(cancelledRun().slice(1));
    await run;
    expect(slot(slotId).durableRun).toBeNull();
  });

  it('a new conversation: the request is kept until the run is named, then that run is cancelled', async () => {
    const api = fakeApi({
      [`POST /api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}/cancel`]: {
        status: 200,
        data: { receipt: { commandId: 'c1' } },
      },
    });
    const stream = gatedStream();
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.setState({ activeSlotId: slotId });
    const { conversationId: _none, ...fresh } = turn;
    const run = streamMessageForSlot(slotId, 'Question', { ...fresh });
    await vi.waitFor(() => expect(slot(slotId).routePending).toBe(true));

    cancelStreamForSlot(slotId);
    expect(stream.signal()?.aborted, 'aborting would leave the admitted run going unseen').toBe(false);
    const frames = asOwner(cancelledRun());
    stream.release(frames.slice(0, 1));
    await vi.waitFor(() =>
      expect(api.sent.map((r) => r.url)).toContain(`/api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}/cancel`),
    );
    stream.release(frames.slice(1));
    await run;
    expect(slot(slotId).isStreaming).toBe(false);
  });
});

describe('LAT2 FR-1, FR-5, FR-6: regenerate is one request on the regenerate route', () => {
  const ANSWER_ID = 'answer-9';
  const REGENERATE_URL = `/api/v1/agents/${AGENT}/conversations/${SESSION_ID}/message/${ANSWER_ID}/regenerate`;
  const MODEL = { modelKey: 'm1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' };

  function regenerateSlot(extra: Record<string, unknown> = {}): string {
    const slotId = newSlot();
    useChatStore.getState().updateSlot(slotId, { threadAgentId: AGENT, ...extra });
    return slotId;
  }

  it('FR-1, FR-5: Stop before the first frame keeps the request, then cancels the durable run it names; never …/runs', async () => {
    const api = fakeApi({
      [`POST /api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}/cancel`]: {
        status: 200,
        data: { receipt: { commandId: 'c1' } },
      },
    });
    const stream = gatedStream();
    const slotId = regenerateSlot();
    const run = streamRegenerateForSlot(slotId, ANSWER_ID, MODEL);
    await vi.waitFor(() => expect(slot(slotId).routePending).toBe(true));

    cancelStreamForSlot(slotId);
    expect(stream.signal()?.aborted, 'the request is kept: a run may already be admitted').toBe(false);
    const frames = asOwner(cancelledRun());
    stream.release(frames.slice(0, 1));
    await vi.waitFor(() =>
      expect(api.sent.map((r) => r.url)).toContain(`/api/v1/agents/${AGENT}/conversations/${SESSION_ID}/runs/${RUN_ID}/cancel`),
    );
    stream.release(frames.slice(1));
    await run;
    expect(streamRequests(), 'one request on the regenerate route').toEqual([expect.stringContaining(REGENERATE_URL)]);
    expect(api.sent.filter((r) => r.method === 'POST' && r.url.endsWith('/runs')), 'no …/runs admission').toEqual([]);
    // The answer settles after the conversation reload that follows RUN_FINISHED.
    await vi.waitFor(() => expect(slot(slotId).isStreaming).toBe(false));
  });

  it('FR-5: when today’s loop answers, Stop sends today’s cancel of this regenerate’s run id', async () => {
    const api = fakeApi({
      [`POST /api/v1/agents/${AGENT}/conversations/${SESSION_ID}/cancel`]: { status: 200, data: { cancelled: true } },
    });
    const stream = gatedStream();
    const slotId = regenerateSlot();
    const run = streamRegenerateForSlot(slotId, ANSWER_ID, MODEL);
    await vi.waitFor(() => expect(slot(slotId).routePending).toBe(true));
    const runId = slot(slotId).runId;

    cancelStreamForSlot(slotId);
    stream.release(cancelledRun().slice(0, 1));
    await vi.waitFor(() => expect(api.sent.find((r) => r.url.endsWith('/cancel'))?.body).toEqual({ runId }));
    stream.release(cancelledRun().slice(1));
    await run;
    expect(slot(slotId).durableRun).toBeNull();
  });

  it('FR-6: a project conversation is the same one request; the server routes it', async () => {
    const api = fakeApi({});
    fetchMock.mockImplementation(async (_url, init) => sseResponse({ chunks: [toWire(cancelledRun())], signal: init?.signal }));
    const slotId = regenerateSlot({ projectId: 'p-1' });
    await streamRegenerateForSlot(slotId, ANSWER_ID, MODEL);
    expect(streamRequests()).toEqual([expect.stringContaining(REGENERATE_URL)]);
    expect(api.sent.filter((r) => r.method === 'POST')).toEqual([]);
  });
});
