/**
 * The durable run transport (S7) against a fake `GET …/runs/:runId/stream`:
 * the real `streamSSEGet` over a faked `fetch` that serves a run's frames
 * after `Last-Event-ID`, as the Node projection does. A live stack runs the
 * same cases in `tests/e2e/chat/chat-durable-stream.spec.ts`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  installMemoryStorage,
  jsonResponse,
  jwtExpiringIn,
  sseResponse,
} from '@/lib/api/__tests__/sse-response';
import type { MessagePart, SSEAnswerChunkEvent, SSEStatusEvent } from '../types';
import type { StreamMessageCallbacks } from '../api';
import {
  ANSWER,
  RUN_ID,
  SESSION_ID,
  completedRun,
  finalConversation,
  markedRestartRun,
  restartedRun,
  runError,
  toWire,
  type DurableFrame,
} from './durable-frames';

installMemoryStorage('localStorage');
const sessionBacking = installMemoryStorage('sessionStorage');

const logoutAndRedirect = vi.fn();
vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  const rules = await vi.importActual<typeof import('@/app/(main)/chat/approval-ask-rules')>('@/app/(main)/chat/approval-ask-rules');
  const runErrors = await vi.importActual<typeof import('@/app/(main)/chat/run-error-extras')>('@/app/(main)/chat/run-error-extras');
  return { useAuthStore: auth.useAuthStore, approvalAskRules: rules.approvalAskRules, runErrorExtras: runErrors.runErrorExtras, logoutAndRedirect: () => logoutAndRedirect() };
});

const apiGet = vi.fn();
const apiPost = vi.fn();
vi.mock('@/lib/api', async () => {
  const streaming = await vi.importActual<typeof import('@/lib/api/streaming')>('@/lib/api/streaming');
  return {
    apiClient: { get: (...args: unknown[]) => apiGet(...args), post: (...args: unknown[]) => apiPost(...args) },
    streamSSEGet: streaming.streamSSEGet,
    streamSSERequest: streaming.streamSSERequest,
  };
});

const { useAuthStore } = await import('@/config');
const { followDurableRun, reconnectDelayMs } = await import('../durable-run-stream');
const { STREAM_ERROR_MESSAGES } = await import('@/lib/api/stream-errors');
const { i18n } = await import('@/lib/i18n');

const STREAM_URL = `/api/v1/agents/agent-1/conversations/${SESSION_ID}/runs/${RUN_ID}/stream`;
const TARGET = { agentId: 'agent-1', sessionId: SESSION_ID, runId: RUN_ID };

type Connection =
  | { status: number; body?: unknown }
  /** Send this many of the remaining frames, then end as told (default: all, then close). */
  | { take?: number; then?: 'fail' | 'close' | 'hang' };

/** One run as the Node stream route serves it, replaying after `Last-Event-ID`. */
class FakeRunServer {
  readonly requests: Array<{ url: string; lastEventId: string | null }> = [];
  private readonly plan: Connection[];

  constructor(
    private readonly frames: DurableFrame[],
    plan: Connection[] = [],
  ) {
    this.plan = [...plan];
  }

  fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const headers = (init?.headers ?? {}) as Record<string, string>;
    const lastEventId = headers['Last-Event-ID'] ?? null;
    this.requests.push({ url, lastEventId });
    const step = this.plan.shift() ?? {};
    if ('status' in step) return jsonResponse(step.status, step.body ?? {});
    const from = lastEventId === null ? 0 : this.frames.findIndex((f) => f.id === lastEventId) + 1;
    const remaining = this.frames.slice(from);
    const sent = remaining.slice(0, step.take ?? remaining.length);
    return sseResponse({
      chunks: sent.map((frame) => toWire([frame])),
      ...(step.then === 'fail' ? { failWith: new TypeError('network error') } : {}),
      ...(step.then === 'hang' ? { hang: true } : {}),
      signal: init?.signal ?? null,
    });
  };

  /** Frames a connection starting after `lastEventId` would get. */
  firstFrameAfter(lastEventId: string | null): DurableFrame | undefined {
    const from = lastEventId === null ? 0 : this.frames.findIndex((f) => f.id === lastEventId) + 1;
    return this.frames[from];
  }
}

interface Tab {
  callbacks: StreamMessageCallbacks;
  controller: AbortController;
  parts: MessagePart[];
  accumulated: string;
  statuses: SSEStatusEvent[];
  errors: Error[];
  completed: Array<Record<string, unknown>>;
  partsCalls: number;
}

function openTab(): Tab {
  const controller = new AbortController();
  const tab: Tab = {
    controller,
    parts: [],
    accumulated: '',
    statuses: [],
    errors: [],
    completed: [],
    partsCalls: 0,
    callbacks: {} as StreamMessageCallbacks,
  };
  tab.callbacks = {
    signal: controller.signal,
    onParts: (parts) => {
      tab.partsCalls += 1;
      tab.parts = JSON.parse(JSON.stringify(parts)) as MessagePart[];
    },
    onChunk: (data: SSEAnswerChunkEvent) => {
      tab.accumulated = data.accumulated;
    },
    onStatus: (status) => tab.statuses.push(status),
    onError: (error) => tab.errors.push(error),
    onComplete: (data) => tab.completed.push(data.conversation as unknown as Record<string, unknown>),
  };
  return tab;
}

// More fake time than any case's waits at full jitter: RC-1's 20 refusals wait up to 240.5 s (5 doubling
// delays, then 15 at the 15 s cap). A budget below that failed whenever the random jitter summed past it.
const SETTLE_STEPS = 1_200;
const SETTLE_STEP_MS = 500;

/** Runs fake timers until the promise settles (backoff waits are timers). */
async function settle(promise: Promise<void>): Promise<void> {
  let done = false;
  void promise.finally(() => {
    done = true;
  });
  for (let i = 0; i < SETTLE_STEPS && !done; i += 1) {
    await vi.advanceTimersByTimeAsync(SETTLE_STEP_MS);
  }
  expect(done, 'transport settled').toBe(true);
  await promise;
}

const texts = (parts: MessagePart[]) => parts.filter((p) => p.type === 'text').map((p) => p.content);

function logLines(): string[] {
  const calls = [
    ...vi.mocked(console.info).mock.calls,
    ...vi.mocked(console.warn).mock.calls,
    ...vi.mocked(console.error).mock.calls,
  ];
  return calls.map((args) => JSON.stringify(args));
}

function logged(event: string): Array<Record<string, unknown>> {
  return [
    ...vi.mocked(console.info).mock.calls,
    ...vi.mocked(console.warn).mock.calls,
    ...vi.mocked(console.error).mock.calls,
  ]
    .filter((args) => args[0] === `[agent-harness] ${event}`)
    .map((args) => args[1] as Record<string, unknown>);
}

let server: FakeRunServer;
const fetchMock = vi.fn<typeof fetch>();

function serve(frames: DurableFrame[], plan: Connection[] = []): FakeRunServer {
  server = new FakeRunServer(frames, plan);
  fetchMock.mockImplementation(server.fetch);
  return server;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
  fetchMock.mockReset();
  apiGet.mockReset();
  apiPost.mockReset();
  logoutAndRedirect.mockReset();
  sessionBacking.clear();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600) });
  vi.spyOn(console, 'info').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('followDurableRun — live stream', () => {
  it('streams a run from its first frame to RUN_FINISHED', async () => {
    serve(completedRun());
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));

    expect(server.requests).toEqual([{ url: STREAM_URL, lastEventId: null }]);
    expect(tab.accumulated).toBe(ANSWER);
    expect(tab.completed).toHaveLength(1);
    expect(tab.errors).toEqual([]);
    expect(sessionBacking.size).toBe(0);
    expect(logged('stream.subscribe')).toEqual([expect.objectContaining({ runId: RUN_ID, resume: 'start' })]);
    expect(logged('stream.finished')).toHaveLength(1);
  });
});

describe('followDurableRun — reconnect (F-5, F-7)', () => {
  it('reconnects mid-message with Last-Event-ID and renders no duplicate or missing text', async () => {
    const frames = completedRun();
    const cut = frames.findIndex((f) => (f.data as { delta?: string }).delta === 'The answer') + 1;
    serve(frames, [{ take: cut, then: 'fail' }]);
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));

    expect(server.requests.map((r) => r.lastEventId)).toEqual([null, frames[cut - 1]?.id]);
    expect(tab.accumulated).toBe(ANSWER);
    expect(texts(tab.parts)).toEqual(['Let me search.', ANSWER]);
    expect(tab.completed).toHaveLength(1);
    expect(tab.errors).toEqual([]);
    expect(logged('stream.reconnect')).toEqual([
      expect.objectContaining({ attempt: 1, reason: 'network', lastEventId: frames[cut - 1]?.id }),
    ]);
    expect(logged('stream.resumed')).toEqual([expect.objectContaining({ attempt: 1 })]);
  });

  it('shows "Reconnecting…" while reconnecting and restores the previous status once back', async () => {
    const frames = completedRun();
    const cut = frames.findIndex((f) => f.event === 'TOOL_CALL_START') + 1;
    serve(frames, [{ take: cut, then: 'close' }, { take: 0, then: 'hang' }]);
    const tab = openTab();
    const following = followDurableRun(TARGET, tab.callbacks);
    await vi.advanceTimersByTimeAsync(2_000);

    const reconnecting = { status: 'reconnecting', message: i18n.t('chatStream.reconnecting') };
    expect(tab.statuses.at(-2)).toEqual(reconnecting);
    expect(tab.statuses.at(-1)).toEqual(tab.statuses.at(-3));
    expect(logged('stream.reconnect')[0]).toEqual(expect.objectContaining({ reason: 'closed_without_terminal' }));
    tab.controller.abort();
    await settle(following);
    expect(tab.errors).toEqual([]);
  });

  it('treats a connection silent past three missed heartbeats as dead and resumes it', async () => {
    const frames = completedRun();
    const cut = frames.findIndex((f) => (f.data as { delta?: string }).delta === 'The answer') + 1;
    serve(frames, [{ take: cut, then: 'hang' }]);
    const tab = openTab();
    const following = followDurableRun(TARGET, tab.callbacks);
    await vi.advanceTimersByTimeAsync(44_000);
    expect(server.requests).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2_000);
    await settle(following);

    expect(server.requests.map((r) => r.lastEventId)).toEqual([null, frames[cut - 1]?.id]);
    expect(tab.accumulated).toBe(ANSWER);
    expect(tab.completed).toHaveLength(1);
    expect(tab.errors).toEqual([]);
    expect(logged('stream.reconnect')).toEqual([expect.objectContaining({ attempt: 1, reason: 'idle' })]);
  });

  it('retries 429 and 5xx refusals', async () => {
    serve(completedRun(), [{ status: 503 }, { status: 429 }]);
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(server.requests).toHaveLength(3);
    expect(tab.completed).toHaveLength(1);
    expect(logged('stream.reconnect').map((f) => f.reason)).toEqual(['http_503', 'http_429']);
  });
});

describe('followDurableRun — reconnect storm (F-6)', () => {
  it.each([
    ['the least jitter', 0],
    ['the most jitter', 1],
    ['random jitter', null],
  ])('UX3 RC-1: backs off exponentially with a cap and, while the page is visible, keeps reconnecting past a rolling restart (%s)', async (_name, jitter) => {
    if (jitter !== null) vi.spyOn(Math, 'random').mockReturnValue(jitter);
    // 20 refusals at the 15 s cap is several minutes: longer than any rolling restart (A-31: 60 s).
    serve(completedRun(), Array.from({ length: 20 }, () => ({ status: 503 })));
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));

    expect(server.requests).toHaveLength(21);
    const delays = logged('stream.reconnect').map((f) => f.delayMs as number);
    expect(delays).toHaveLength(20);
    delays.forEach((delay, i) => {
      const ceiling = Math.min(15_000, 500 * 2 ** i);
      expect(delay).toBeGreaterThanOrEqual(ceiling / 2);
      expect(delay).toBeLessThanOrEqual(ceiling);
    });
    expect(tab.errors).toEqual([]);
    expect(tab.completed).toHaveLength(1);
    expect(logged('stream.terminal_error')).toEqual([]);
  });

  it('UX3 RC-2: hidden past the budget, it waits for the page instead of giving up, and reconnects as soon as it is shown', async () => {
    serve(completedRun(), Array.from({ length: 9 }, () => ({ status: 503 })));
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const tab = openTab();
    const following = followDurableRun(TARGET, tab.callbacks);
    for (let i = 0; i < 400; i += 1) await vi.advanceTimersByTimeAsync(500);
    expect(server.requests).toHaveLength(9);
    expect(tab.errors).toEqual([]);
    expect(logged('stream.reconnect_paused')).toEqual([expect.objectContaining({ reason: 'page_hidden' })]);

    visibility.mockReturnValue('visible');
    document.dispatchEvent(new Event('visibilitychange'));
    await settle(following);
    expect(tab.completed).toHaveLength(1);
    expect(tab.errors).toEqual([]);
    visibility.mockRestore();
  });

  it('UX3 RC-2: coming back online cuts the wait short', async () => {
    serve(completedRun(), [{ status: 503 }, { status: 503 }, { status: 503 }, { status: 503 }, { status: 503 }]);
    const tab = openTab();
    const following = followDurableRun(TARGET, tab.callbacks);
    for (let i = 0; i < 20; i += 1) await vi.advanceTimersByTimeAsync(0);
    const before = server.requests.length;
    window.dispatchEvent(new Event('online'));
    for (let i = 0; i < 20; i += 1) await vi.advanceTimersByTimeAsync(0);
    expect(server.requests.length).toBe(before + 1);
    await settle(following);
  });

  it('resets the budget whenever a connection delivers frames', async () => {
    const frames = completedRun();
    serve(frames, Array.from({ length: frames.length }, () => ({ take: 1, then: 'fail' as const })));
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(server.requests.length).toBe(frames.length);
    expect(tab.accumulated).toBe(ANSWER);
    expect(tab.completed).toHaveLength(1);
    expect(tab.errors).toEqual([]);
  });

  it('keeps the delay within [ceiling/2, ceiling]', () => {
    expect(reconnectDelayMs(1, () => 0)).toBe(250);
    expect(reconnectDelayMs(1, () => 1)).toBe(500);
    expect(reconnectDelayMs(10, () => 1)).toBe(15_000);
  });
});

describe('followDurableRun — reload (F-2)', () => {
  it('resumes from the saved id after a reload, with no replay from the first frame', async () => {
    const frames = completedRun();
    const cut = frames.findIndex((f) => (f.data as { delta?: string }).delta === ' is 4') + 1;
    serve(frames, [{ take: cut, then: 'hang' }]);
    const before = openTab();
    const first = followDurableRun(TARGET, before.callbacks);
    await vi.advanceTimersByTimeAsync(100);
    window.dispatchEvent(new Event('pagehide'));
    const saved = JSON.parse(sessionBacking.get(`pipeshub.agentRunStream.${RUN_ID}`) ?? '{}') as {
      lastEventId?: string;
    };
    expect(saved.lastEventId).toBe(frames[cut - 1]?.id);
    before.controller.abort();
    await settle(first);

    const after = openTab();
    await settle(followDurableRun(TARGET, after.callbacks));

    expect(server.requests[1]).toEqual({ url: STREAM_URL, lastEventId: frames[cut - 1]?.id });
    expect(texts(after.parts)).toEqual(['Let me search.', ANSWER]);
    expect(after.accumulated).toBe(ANSWER);
    expect(after.completed).toHaveLength(1);
    expect(logged('stream.subscribe')[1]).toEqual(
      expect.objectContaining({ resume: 'saved_state', lastEventId: frames[cut - 1]?.id }),
    );
    expect(sessionBacking.size).toBe(0);
  });

  it('shows the saved answer at once, before any new frame arrives', async () => {
    const frames = completedRun();
    const cut = frames.findIndex((f) => (f.data as { delta?: string }).delta === ' is 4') + 1;
    serve(frames, [{ take: cut, then: 'hang' }, { take: 0, then: 'hang' }]);
    const before = openTab();
    const first = followDurableRun(TARGET, before.callbacks);
    await vi.advanceTimersByTimeAsync(100);
    before.controller.abort();
    await settle(first);

    const after = openTab();
    const second = followDurableRun(TARGET, after.callbacks);
    await vi.advanceTimersByTimeAsync(100);
    expect(after.accumulated).toBe('The answer is 4');
    expect(texts(after.parts)).toEqual(['Let me search.', 'The answer is 4']);
    after.controller.abort();
    await settle(second);
  });

  it('replays from the start when this tab cannot save its resume state', async () => {
    const frames = completedRun();
    const cut = 6;
    serve(frames, [{ take: cut, then: 'hang' }]);
    const setItem = vi.spyOn(window.sessionStorage, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });
    const before = openTab();
    const first = followDurableRun(TARGET, before.callbacks);
    await vi.advanceTimersByTimeAsync(100);
    before.controller.abort();
    await settle(first);
    setItem.mockRestore();

    const after = openTab();
    await settle(followDurableRun(TARGET, after.callbacks));
    expect(server.requests[1]?.lastEventId).toBeNull();
    expect(after.accumulated).toBe(ANSWER);
    expect(texts(after.parts)).toEqual(['Let me search.', ANSWER]);
    expect(logged('stream.resume_state_unavailable')).toHaveLength(1);
  });
});

describe('followDurableRun — message restarted under a new id (F-8)', () => {
  it('replaces the abandoned message and logs the restart', async () => {
    serve(restartedRun());
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(texts(tab.parts)).toEqual(['Let me search.', ANSWER]);
    expect(tab.accumulated).toBe(ANSWER);
    expect(logged('stream.message_restarted')).toEqual([
      expect.objectContaining({ previousMessageId: 'msg_b', messageId: 'msg_b_restart', via: 'order' }),
    ]);
  });

  it('replaces the message a restart marker names, and logs it as marked (MR-2)', async () => {
    serve(markedRestartRun('gap'));
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(texts(tab.parts)).toEqual(['Let me search.', ANSWER]);
    expect(tab.accumulated).toBe(ANSWER);
    expect(logged('stream.message_restarted')).toEqual([
      expect.objectContaining({ previousMessageId: 'msg_b', messageId: 'msg_b_restart', via: 'marker' }),
    ]);
  });
});

describe('followDurableRun — terminal outcomes (F-9, F-10, F-11, F-14)', () => {
  it('reports a server RUN_ERROR once and does not reconnect', async () => {
    serve(runError());
    sessionBacking.set(`pipeshub.agentRunStream.${RUN_ID}`, 'stale');
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(server.requests).toHaveLength(1);
    expect(tab.errors.map((e) => e.message)).toEqual(['The model is not available right now.']);
    expect(logged('stream.run_error')).toEqual([expect.objectContaining({ code: 'model_unavailable' })]);
    expect(sessionBacking.size).toBe(0);
  });

  it('ends with the session-expired message on a 401, without reconnecting', async () => {
    serve(completedRun(), [{ status: 401 }]);
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(server.requests).toHaveLength(1);
    expect(tab.errors.map((e) => e.message)).toEqual([STREAM_ERROR_MESSAGES.sessionExpired]);
    expect(logged('stream.terminal_error')).toEqual([expect.objectContaining({ reason: 'session_expired' })]);
  });

  it('ends with the session-expired message when the token cannot be refreshed', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(-60) });
    fetchMock.mockResolvedValue(jsonResponse(401, {}));
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(fetchMock.mock.calls.map((call) => String(call[0])).some((url) => url.includes('/stream'))).toBe(false);
    expect(tab.errors.map((e) => e.message)).toEqual([STREAM_ERROR_MESSAGES.sessionExpired]);
  });

  it('treats 403 as final', async () => {
    serve(completedRun(), [{ status: 403 }]);
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(server.requests).toHaveLength(1);
    expect(tab.errors).toHaveLength(1);
    expect(logged('stream.terminal_error')).toEqual([expect.objectContaining({ reason: 'http_403' })]);
  });

  it('loads the conversation and shows its final answer when the stream is gone (404)', async () => {
    serve(completedRun(), [{ status: 404 }]);
    apiGet.mockResolvedValueOnce({ data: { conversation: { ...finalConversation(), activeRunId: null } } });
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(apiGet).toHaveBeenCalledWith(`/api/v1/agents/agent-1/conversations/${SESSION_ID}`, expect.any(Object));
    expect(tab.completed).toEqual([expect.objectContaining({ _id: SESSION_ID })]);
    expect(tab.errors).toEqual([]);
    expect(logged('stream.not_found')).toHaveLength(1);
  });

  it('reports an error when the stream is gone and so is the conversation', async () => {
    serve(completedRun(), [{ status: 404 }]);
    apiGet.mockRejectedValueOnce(Object.assign(new Error('Not found'), { statusCode: 404 }));
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(tab.completed).toEqual([]);
    expect(tab.errors.map((e) => e.message)).toEqual([STREAM_ERROR_MESSAGES.unavailable]);
  });
});

describe('followDurableRun — tabs (F-1, F-12)', () => {
  it('closing the tab only unsubscribes; reopening shows the run live, then its answer', async () => {
    const frames = completedRun();
    serve(frames, [{ take: 8, then: 'hang' }]);
    const closed = openTab();
    const first = followDurableRun(TARGET, closed.callbacks);
    await vi.advanceTimersByTimeAsync(100);
    closed.controller.abort();
    await settle(first);
    expect(apiPost).not.toHaveBeenCalled();
    expect(logged('stream.unsubscribe')).toEqual([expect.objectContaining({ reason: 'aborted' })]);

    sessionBacking.clear();
    const reopened = openTab();
    await settle(followDurableRun(TARGET, reopened.callbacks));
    expect(server.requests[1]?.lastEventId).toBeNull();
    expect(reopened.statuses.length).toBeGreaterThan(0);
    expect(reopened.accumulated).toBe(ANSWER);
    expect(reopened.completed).toHaveLength(1);
  });

  it('two tabs on one run render the same answer; closing one leaves the other running', async () => {
    const frames = completedRun();
    serve(frames, [{ take: 5, then: 'hang' }]);
    const a = openTab();
    const b = openTab();
    const followingA = followDurableRun(TARGET, a.callbacks);
    const followingB = followDurableRun(TARGET, b.callbacks);
    await vi.advanceTimersByTimeAsync(100);
    a.controller.abort();
    await settle(followingA);
    await settle(followingB);

    expect(apiPost).not.toHaveBeenCalled();
    expect(b.accumulated).toBe(ANSWER);
    expect(b.completed).toHaveLength(1);

    serve(frames);
    sessionBacking.clear();
    const c = openTab();
    await settle(followDurableRun(TARGET, c.callbacks));
    expect(c.parts).toEqual(b.parts);
  });
});

describe('followDurableRun — logs (F-15)', () => {
  it('never logs message content', async () => {
    const secret = 'SENTINEL-4f1c answer text';
    const frames = restartedRun(secret);
    serve(frames, [{ take: 16, then: 'fail' }, { status: 503 }]);
    const tab = openTab();
    await settle(followDurableRun(TARGET, tab.callbacks));
    expect(tab.accumulated).toBe(secret);
    expect(logLines().length).toBeGreaterThan(3);
    for (const line of logLines()) {
      expect(line).not.toContain('SENTINEL');
      expect(line).not.toContain('Let me search');
    }
  });
});

describe('followDurableRun — abort', () => {
  it('stops without an error when the caller aborts mid-stream', async () => {
    serve(completedRun(), [{ take: 3, then: 'hang' }]);
    const tab = openTab();
    const following = followDurableRun(TARGET, tab.callbacks);
    await vi.advanceTimersByTimeAsync(100);
    tab.controller.abort();
    await settle(following);
    expect(tab.errors).toEqual([]);
    expect(server.requests).toHaveLength(1);
  });
});
