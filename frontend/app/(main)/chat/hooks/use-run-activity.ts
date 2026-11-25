'use client';

import { useEffect, useReducer } from 'react';
import { streamSSEGet } from '@/lib/api';
import { STREAM_ERROR_MESSAGES, StreamError } from '@/lib/api/stream-errors';
import { enqueueActivityEvent, reduceActivity, toActivityEvent } from '../activity';
import {
  MAX_RECONNECTS_WITHOUT_PROGRESS,
  STREAM_IDLE_TIMEOUT_MS,
  durableSessionBase,
  durableStreamLog,
  isPermanentRefusal,
  pageVisible,
  reconnectDelayMs,
  waitToRetry,
} from '../durable-run-stream';
import type { ActivityEvent, ActivityView, DurableAgentRunHandle } from '../types';

/**
 * `connecting`: no snapshot yet. `reconnecting`: the stream dropped and is
 * being retried (the last view stays on screen). `ended`: the run finished and
 * the server closed. `unavailable`: refused (404, flag off, not the owner).
 */
export type ActivityConnection = 'connecting' | 'live' | 'reconnecting' | 'ended' | 'unavailable';

export interface ActivityStreamCallbacks {
  onEvent: (event: ActivityEvent) => void;
  onConnection: (connection: ActivityConnection) => void;
  signal: AbortSignal;
}

export function activityUrl(run: DurableAgentRunHandle): string {
  return `${durableSessionBase(run)}/runs/${encodeURIComponent(run.runId)}/activity`;
}

/**
 * Follows `GET …/runs/:runId/activity` until `end`, a refusal, or abort. A
 * reconnect is a new request whose first event is a fresh snapshot, so no
 * cursor is kept (UX5 D-3).
 */
export async function followRunActivity(run: DurableAgentRunHandle, callbacks: ActivityStreamCallbacks): Promise<void> {
  const { signal, onEvent, onConnection } = callbacks;
  const url = activityUrl(run);
  const ids = { runId: run.runId, sessionId: run.sessionId };
  let failures = 0;
  let ended = false;
  onConnection('connecting');
  while (!signal.aborted) {
    const attempt: { error: Error | null; progressed: boolean } = { error: null, progressed: false };
    await streamSSEGet(url, {
      idleTimeoutMs: STREAM_IDLE_TIMEOUT_MS,
      signal,
      onEvent: (sse) => {
        if (ended) return;
        const event = toActivityEvent(sse.event, sse.data);
        if (!event) return;
        attempt.progressed = true;
        if (event.type === 'snapshot') onConnection('live');
        onEvent(event);
        if (event.type === 'end') ended = true;
      },
      onError: (error) => {
        attempt.error = error;
      },
    });
    if (ended) {
      onConnection('ended');
      return;
    }
    if (signal.aborted) return;

    const { error } = attempt;
    const status = error instanceof StreamError ? error.status : undefined;
    if (status === 401 || isPermanentRefusal(status) || error?.message === STREAM_ERROR_MESSAGES.sessionExpired) {
      durableStreamLog.warn('activity.unavailable', { ...ids, status: status ?? null });
      onConnection('unavailable');
      return;
    }
    if (attempt.progressed) failures = 0;
    failures += 1;
    onConnection('reconnecting');
    if (failures > MAX_RECONNECTS_WITHOUT_PROGRESS && !pageVisible()) {
      await waitToRetry(null, signal);
      failures = 0;
      continue;
    }
    const delayMs = reconnectDelayMs(failures);
    durableStreamLog.warn('activity.reconnect', { ...ids, attempt: failures, delayMs, status: status ?? null });
    await waitToRetry(delayMs, signal);
  }
}

export interface RunActivityState {
  view: ActivityView | null;
  connection: ActivityConnection;
}

type Action =
  | { type: 'reset' }
  | { type: 'events'; events: ActivityEvent[] }
  | { type: 'connection'; connection: ActivityConnection };

/** How long events wait to be folded together: one frame, so a burst renders once a frame (PF-17). */
export const ACTIVITY_FOLD_MS = 16;

const INITIAL: RunActivityState = { view: null, connection: 'connecting' };

function reducer(state: RunActivityState, action: Action): RunActivityState {
  switch (action.type) {
    case 'reset':
      return INITIAL;
    case 'events': {
      const view = action.events.reduce(reduceActivity, state.view);
      return view === state.view ? state : { ...state, view };
    }
    case 'connection':
      return action.connection === state.connection ? state : { ...state, connection: action.connection };
    default:
      return state;
  }
}

/**
 * The panel's live view of one run. The stream is open only while `open`
 * (D-3: nothing is spent unless someone looks); the view of a closed panel is
 * kept so reopening shows it until the fresh snapshot lands.
 */
export function useRunActivity(run: DurableAgentRunHandle | null, open: boolean): RunActivityState {
  const [state, dispatch] = useReducer(reducer, INITIAL);
  const agentId = run?.agentId;
  const sessionId = run?.sessionId;
  const runId = run?.runId;

  useEffect(() => {
    dispatch({ type: 'reset' });
  }, [agentId, sessionId, runId]);

  useEffect(() => {
    if (!open || !sessionId || !runId) return undefined;
    const controller = new AbortController();
    let pending: ActivityEvent[] = [];
    let timer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      timer = null;
      if (!pending.length) return;
      const events = pending;
      pending = [];
      dispatch({ type: 'events', events });
    };
    void followRunActivity(
      { ...(agentId ? { agentId } : {}), sessionId, runId },
      {
        signal: controller.signal,
        onEvent: (event) => {
          enqueueActivityEvent(pending, event);
          if (timer === null) timer = setTimeout(flush, ACTIVITY_FOLD_MS);
        },
        onConnection: (connection) => {
          // A connection change is shown with the events before it, never ahead of them.
          flush();
          dispatch({ type: 'connection', connection });
        },
      },
    );
    return () => {
      controller.abort();
      if (timer !== null) clearTimeout(timer);
    };
  }, [open, agentId, sessionId, runId]);

  return state;
}
