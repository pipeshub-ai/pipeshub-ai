import { apiClient, streamSSEGet, type SSEEvent } from '@/lib/api';
import { CHAT_STREAM_ERROR_MESSAGES, STREAM_ERROR_MESSAGES, StreamError, StreamIdleError } from '@/lib/api/stream-errors';
import { i18n } from '@/lib/i18n';
import {
  createAGUIEventHandler,
  type AGUIEventHandler,
  type AGUIHandlerCheckpoint,
  type AGUIStreamTracking,
} from './agui-event-handler';
import type { StreamMessageCallbacks } from './api';
import type { ConversationCompleteData, DurableAgentRunHandle, SSEStatusEvent } from './types';

/**
 * Follows a durable agent run over `GET …/runs/:runId/stream` (REPORT 6.2.4,
 * 8). Frames are the legacy AG-UI vocabulary with an `id:` each; the server
 * replays exactly the frames after `Last-Event-ID`, so a reconnect sends the
 * last id seen and the handler keeps its state. Across a reload, the handler
 * state and that id are kept in this tab's sessionStorage. Aborting only
 * unsubscribes: the run goes on, and Stop is a separate cancel command.
 */

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 15_000;
/**
 * Consecutive failed connections, with no frame received, after which a
 * hidden page stops retrying until it is shown or back online (UX3). A
 * visible page keeps retrying: a rolling restart can take a minute or more.
 */
export const MAX_RECONNECTS_WITHOUT_PROGRESS = 8;
const CHECKPOINT_INTERVAL_MS = 1_000;
// The projection sends an SSE heartbeat every 15 s; three missed ones mean a dead connection.
export const STREAM_IDLE_TIMEOUT_MS = 45_000;
const CHECKPOINT_KEY_PREFIX = 'pipeshub.agentRunStream.';
const CHECKPOINT_VERSION = 1;

type LogFields = Record<string, string | number | boolean | null>;

/**
 * Transport events for support: ids and counters only, never message text.
 * The frontend has no telemetry sink; the console is where these are read.
 */
export const durableStreamLog = {
  info(event: string, fields: LogFields): void {
    console.info(`[agent-harness] ${event}`, fields);
  },
  warn(event: string, fields: LogFields): void {
    console.warn(`[agent-harness] ${event}`, fields);
  },
  error(event: string, fields: LogFields): void {
    console.error(`[agent-harness] ${event}`, fields);
  },
};

interface SavedStream {
  v: number;
  lastEventId: string;
  view: AGUIHandlerCheckpoint;
}

function sessionStore(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function readSaved(runId: string): SavedStream | null {
  try {
    const raw = sessionStore()?.getItem(CHECKPOINT_KEY_PREFIX + runId);
    if (!raw) return null;
    const saved = JSON.parse(raw) as Partial<SavedStream>;
    if (saved.v !== CHECKPOINT_VERSION || typeof saved.lastEventId !== 'string' || !saved.view) {
      return null;
    }
    return saved as SavedStream;
  } catch {
    return null;
  }
}

/** False when the browser refused (quota, privacy mode); the stream then replays from the start after a reload. */
function writeSaved(runId: string, saved: SavedStream): boolean {
  try {
    const store = sessionStore();
    if (!store) return false;
    store.setItem(CHECKPOINT_KEY_PREFIX + runId, JSON.stringify(saved));
    return true;
  } catch {
    return false;
  }
}

/** This tab resumes the run after frames it already saw: anything they built must be restored another way. */
export function hasSavedDurableStream(runId: string): boolean {
  return readSaved(runId) !== null;
}

function clearSaved(runId: string): void {
  try {
    sessionStore()?.removeItem(CHECKPOINT_KEY_PREFIX + runId);
  } catch {
    // Nothing to clean up when storage is unavailable.
  }
}

export function durableSessionBase(target: Pick<DurableAgentRunHandle, 'agentId' | 'sessionId'>): string {
  return target.agentId
    ? `/api/v1/agents/${encodeURIComponent(target.agentId)}/conversations/${encodeURIComponent(target.sessionId)}`
    : `/api/v1/conversations/${encodeURIComponent(target.sessionId)}`;
}

export function reconnectDelayMs(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.max(0, attempt - 1));
  return Math.round(ceiling / 2 + (random() * ceiling) / 2);
}

export function pageVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden';
}

/**
 * Until `ms` passed (null: no timer), the page is shown again or back online,
 * or the stream is aborted, whichever comes first (UX3 RC-2).
 */
export function waitToRetry(ms: number | null, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = ms === null ? null : setTimeout(done, ms);
    function onVisible() {
      if (pageVisible()) done();
    }
    function done() {
      if (timer !== null) clearTimeout(timer);
      signal?.removeEventListener('abort', done);
      if (typeof window !== 'undefined') window.removeEventListener('online', done);
      if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisible);
      resolve();
    }
    signal?.addEventListener('abort', done, { once: true });
    if (typeof window !== 'undefined') window.addEventListener('online', done);
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisible);
  });
}

/** A root run's last frame (a child's never ends the stream). */
export function isRootTerminalFrame(event: SSEEvent): boolean {
  const data = event.data as { type?: unknown; parentRunId?: unknown } | undefined;
  const type = data?.type ?? event.event;
  return (type === 'RUN_FINISHED' || type === 'RUN_ERROR') && !data?.parentRunId;
}

function runErrorCode(event: SSEEvent): string | null {
  const data = event.data as { type?: unknown; code?: unknown } | undefined;
  if ((data?.type ?? event.event) !== 'RUN_ERROR') return null;
  return typeof data?.code === 'string' ? data.code : 'unknown';
}

/** A server refusal that another attempt cannot fix. */
export function isPermanentRefusal(status: number | undefined): boolean {
  return status !== undefined && status >= 400 && status < 500 && status !== 408 && status !== 429;
}

/** Where a turn's own response stopped: its handler and the last frame it saw (LAT1). */
export interface DurableStreamResume {
  handler: AGUIEventHandler;
  tracking: AGUIStreamTracking;
  lastEventId: string | null;
}

/**
 * Streams one durable run into `callbacks` until it finishes, fails, or
 * `callbacks.signal` aborts. Never throws for a transport failure: it keeps
 * reconnecting while the run may still be going (UX3), and only a refusal no
 * retry can fix reaches `callbacks.onError`. With `resume`, it continues the
 * chat route's own stream that dropped: same handler, after its last frame.
 */
export async function followDurableRun(
  target: DurableAgentRunHandle,
  callbacks: StreamMessageCallbacks,
  resume?: DurableStreamResume,
): Promise<void> {
  const { runId, sessionId } = target;
  const signal = callbacks.signal;
  const base = durableSessionBase(target);
  const url = `${base}/runs/${encodeURIComponent(runId)}/stream`;
  const ids = { runId, sessionId };

  const saved = resume ? null : readSaved(runId);
  let lastEventId: string | null = resume ? resume.lastEventId : (saved?.lastEventId ?? null);
  let lastStatus: SSEStatusEvent | null = null;
  const handlerCallbacks: StreamMessageCallbacks = {
    ...callbacks,
    onStatus: (status) => {
      lastStatus = status;
      callbacks.onStatus?.(status);
    },
  };
  const tracking: AGUIStreamTracking = resume?.tracking ?? { receivedComplete: false };
  const handler =
    resume?.handler ??
    createAGUIEventHandler(handlerCallbacks, tracking, {
      replaceRestartedMessages: true,
      ...(saved ? { resumeFrom: saved.view } : {}),
      onMessageRestarted: (previousMessageId, messageId, via) => {
        durableStreamLog.info('stream.message_restarted', { ...ids, previousMessageId, messageId, via });
      },
    });

  let storageWorks = true;
  let lastSavedAt = 0;
  const save = () => {
    if (!storageWorks || lastEventId === null) return;
    lastSavedAt = Date.now();
    if (!writeSaved(runId, { v: CHECKPOINT_VERSION, lastEventId, view: handler.checkpoint() })) {
      storageWorks = false;
      clearSaved(runId);
      durableStreamLog.warn('stream.resume_state_unavailable', { ...ids, choice: 'replay_from_start_after_reload' });
    }
  };
  const onPageHide = () => save();
  if (typeof window !== 'undefined') window.addEventListener('pagehide', onPageHide);

  durableStreamLog.info('stream.subscribe', {
    ...ids,
    resume: saved ? 'saved_state' : 'start',
    lastEventId,
  });
  if (saved) handler.publishView();

  let failures = 0;
  let reconnecting = resume !== undefined;
  if (reconnecting) callbacks.onStatus?.({ status: 'reconnecting', message: i18n.t('chatStream.reconnecting') });
  let finished = false;
  try {
    while (!signal?.aborted) {
      const attempt: { error: Error | null; progressed: boolean } = { error: null, progressed: false };
      await streamSSEGet(url, {
        idleTimeoutMs: STREAM_IDLE_TIMEOUT_MS,
        ...(lastEventId !== null ? { headers: { 'Last-Event-ID': lastEventId } } : {}),
        ...(signal ? { signal } : {}),
        onOpen: () => {
          if (!reconnecting) return;
          reconnecting = false;
          durableStreamLog.info('stream.resumed', { ...ids, attempt: failures, lastEventId });
          if (lastStatus) callbacks.onStatus?.(lastStatus);
        },
        onEvent: (event) => {
          if (finished) return;
          handler(event);
          if (event.id) lastEventId = event.id;
          attempt.progressed = true;
          if (isRootTerminalFrame(event)) {
            finished = true;
            const code = runErrorCode(event);
            if (code === null) {
              durableStreamLog.info('stream.finished', { ...ids, lastEventId });
            } else {
              durableStreamLog.warn('stream.run_error', { ...ids, code, lastEventId });
            }
            return;
          }
          if (Date.now() - lastSavedAt >= CHECKPOINT_INTERVAL_MS) save();
        },
        onError: (error) => {
          attempt.error = error;
        },
      });

      if (finished) {
        clearSaved(runId);
        // A root RUN_ERROR{code:'abort'} is not reported by the handler; the turn still has to end.
        if (!tracking.receivedComplete && !tracking.receivedError) {
          callbacks.onError?.(new Error(CHAT_STREAM_ERROR_MESSAGES.interrupted));
        }
        return;
      }
      if (signal?.aborted) break;

      const { error } = attempt;
      const status = error instanceof StreamError ? error.status : undefined;
      if (status === 404) {
        await finishFromConversation(base, target, callbacks);
        clearSaved(runId);
        return;
      }
      if (status === 401 || error?.message === STREAM_ERROR_MESSAGES.sessionExpired) {
        durableStreamLog.warn('stream.terminal_error', { ...ids, reason: 'session_expired', lastEventId });
        callbacks.onError?.(new Error(STREAM_ERROR_MESSAGES.sessionExpired));
        return;
      }
      if (isPermanentRefusal(status)) {
        durableStreamLog.error('stream.terminal_error', { ...ids, reason: `http_${status}`, lastEventId });
        callbacks.onError?.(error ?? new Error(STREAM_ERROR_MESSAGES.unavailable));
        return;
      }

      if (attempt.progressed) failures = 0;
      failures += 1;
      if (!reconnecting) {
        reconnecting = true;
        callbacks.onStatus?.({ status: 'reconnecting', message: i18n.t('chatStream.reconnecting') });
      }
      if (failures > MAX_RECONNECTS_WITHOUT_PROGRESS && !pageVisible()) {
        durableStreamLog.warn('stream.reconnect_paused', {
          ...ids,
          attempts: failures - 1,
          lastEventId,
          reason: 'page_hidden',
        });
        await waitToRetry(null, signal);
        failures = 0;
        continue;
      }
      const delayMs = reconnectDelayMs(failures);
      durableStreamLog.warn('stream.reconnect', {
        ...ids,
        attempt: failures,
        delayMs,
        lastEventId,
        reason:
          status !== undefined
            ? `http_${status}`
            : error instanceof StreamIdleError
              ? 'idle'
              : error
                ? 'network'
                : 'closed_without_terminal',
      });
      await waitToRetry(delayMs, signal);
    }
    save();
    durableStreamLog.info('stream.unsubscribe', { ...ids, lastEventId, reason: 'aborted' });
  } finally {
    if (typeof window !== 'undefined') window.removeEventListener('pagehide', onPageHide);
  }
}

/**
 * The stream no longer exists for this run (404): the conversation holds
 * what the run left, so show that instead of an error.
 */
async function finishFromConversation(
  base: string,
  target: DurableAgentRunHandle,
  callbacks: StreamMessageCallbacks,
): Promise<void> {
  const ids = { runId: target.runId, sessionId: target.sessionId };
  try {
    const { data } = await apiClient.get<{
      conversation: ConversationCompleteData & { activeRunId?: string | null };
    }>(base, { signal: callbacks.signal, suppressErrorToast: true });
    const conversation = data?.conversation;
    if (!conversation || conversation.activeRunId === target.runId) {
      durableStreamLog.error('stream.terminal_error', {
        ...ids,
        reason: conversation ? 'stream_missing_for_active_run' : 'conversation_missing',
      });
      callbacks.onError?.(new Error(STREAM_ERROR_MESSAGES.unavailable));
      return;
    }
    durableStreamLog.warn('stream.not_found', { ...ids, choice: 'show_saved_conversation' });
    callbacks.onAnswerFinal?.();
    callbacks.onComplete?.({
      conversation,
      meta: { requestId: target.runId, timestamp: new Date().toISOString(), duration: 0 },
    });
  } catch (error) {
    if (callbacks.signal?.aborted) return;
    durableStreamLog.error('stream.terminal_error', {
      ...ids,
      reason: 'conversation_unavailable',
      errorType: error instanceof Error ? error.name : typeof error,
    });
    callbacks.onError?.(new Error(STREAM_ERROR_MESSAGES.unavailable));
  }
}
