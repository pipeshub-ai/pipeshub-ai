import type { SSEEngineProgressEvent } from '@/chat/types';
import { memoryLimitOf } from '@/chat/harness-errors';
import { frameOf } from './durable-trail';

/**
 * A job's own live stream (`GET …/runs/{jobRunId}/stream`, ADR-UI-01 D4) folded into what its card, its Needs you
 * line and its open panel read: the engine's progress frames in order, the asks it raised and how each was settled,
 * when it started and ended, and how it ended. Pure, frame by frame, so a replay after a reconnect (the server sends
 * exactly the frames after `Last-Event-ID`) and a whole replay of an ended job give the same state.
 *
 * The job's timeline is not polled any more: the stream carries the steps; one timeline read after the job ends
 * brings what the stream does not (its merge report, its budget, an operator's stop).
 */

export type JobAskStatus = 'pending' | 'approved' | 'denied' | 'expired' | 'closed';

export interface JobAsk {
  interactionId: string;
  toolName: string;
  displayName?: string;
  argsSummary?: string | null;
  status: JobAskStatus;
}

export interface JobStreamState {
  runId: string;
  /** The run's status in `engineJob`'s vocabulary. */
  status: 'queued' | 'running' | 'suspended' | 'completed' | 'failed' | 'cancelled';
  frames: ReadonlyArray<SSEEngineProgressEvent>;
  asks: ReadonlyArray<JobAsk>;
  /** From the frames' own timestamps (ms), else the run's snapshots (`started_at`; the end's last `updated_at`). */
  startedAt?: number;
  finishedAt?: number;
  /** The last snapshot's `updated_at` (ms): the end, when the end frame carries no time of its own. */
  updatedAt?: number;
  /** A failed run's error code, as the stream named it. */
  errorCode?: string;
  /** An out-of-memory end's limit in MB (RUN_ERROR's details), which the card names (R32SECCOMP-FU-4). */
  memoryLimitMb?: number;
  /** The last frame id: a reconnect resumes after it. */
  lastEventId?: string;
  /** The stream sent the run's last frame. */
  ended: boolean;
}

export const emptyJobStream = (runId: string): JobStreamState => ({ runId, status: 'queued', frames: [], asks: [], ended: false });

const str = (value: unknown): string | undefined => (typeof value === 'string' && value ? value : undefined);

function timeOf(frame: Record<string, unknown>): number | undefined {
  const raw = frame.timestamp;
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (typeof raw === 'string') {
    const at = Date.parse(raw);
    if (Number.isFinite(at)) return at;
  }
  return undefined;
}

function settled(decision: string | undefined): JobAskStatus {
  switch (decision) {
    case 'approved':
    case 'answered':
      return 'approved';
    case 'denied':
      return 'denied';
    case 'expired':
      return 'expired';
    default:
      return 'closed';
  }
}

/** One frame (a stream event's data, or a recorded `{ id, frame }`), folded in. Frames of another run are ignored. */
export function reduceJobFrame(state: JobStreamState, input: unknown, id?: string): JobStreamState {
  const frame = frameOf(input);
  if (!frame) return state;
  const raw = frame as unknown as Record<string, unknown>;
  const recordedId = typeof input === 'object' && input !== null ? str((input as { id?: unknown }).id) : undefined;
  const next: JobStreamState = { ...state, ...(id ?? recordedId ? { lastEventId: id ?? recordedId } : {}) };
  const runId = str(raw.runId);
  const at = timeOf(raw);
  switch (frame.type) {
    case 'RUN_STARTED':
      if (runId && runId !== state.runId) return next;
      return { ...next, status: state.status === 'queued' ? 'running' : state.status, ...(at !== undefined && state.startedAt === undefined ? { startedAt: at } : {}) };
    case 'RUN_FINISHED': {
      if (runId && runId !== state.runId) return next;
      // A run that finished on a stop or a failure says so in its last snapshot; the engine's own `finished` frame too.
      const finished = [...state.frames].reverse().find((f) => f.kind === 'finished');
      const failed = finished && finished.data && (finished.data as Record<string, unknown>).success === false;
      const end = at ?? state.updatedAt;
      return { ...next, status: state.status === 'cancelled' ? 'cancelled' : failed ? 'failed' : 'completed', ended: true, ...(end !== undefined ? { finishedAt: end } : {}), asks: closeOpen(state.asks) };
    }
    case 'RUN_ERROR': {
      if (runId && runId !== state.runId) return next;
      const code = str(raw.code);
      const stopped = code === 'abort' || code === 'cancelled';
      const end = at ?? state.updatedAt;
      const limit = memoryLimitOf(raw.details);
      return { ...next, status: stopped ? 'cancelled' : 'failed', ended: true, ...(code && !stopped ? { errorCode: code } : {}), ...(limit !== null && !stopped ? { memoryLimitMb: limit } : {}), ...(end !== undefined ? { finishedAt: end } : {}), asks: closeOpen(state.asks) };
    }
    case 'STATE_SNAPSHOT': {
      const snapshot = (raw.snapshot ?? {}) as Record<string, unknown>;
      if (str(snapshot.run_id) && str(snapshot.run_id) !== state.runId) return next;
      const s = str(snapshot.status);
      // QA-AUTO F5: the durable stream's RUN_STARTED and RUN_FINISHED carry no time; its snapshots do. Without them a
      // job's card read "Done", never "Done in 12s", and "Working", never "Working for 0:12". A queued snapshot's
      // start is when it was handed out, not when it started.
      const started = s && s !== 'queued' ? timeOf({ timestamp: snapshot.started_at }) : undefined;
      const updated = timeOf({ timestamp: snapshot.updated_at });
      const timed: JobStreamState = {
        ...next,
        ...(started !== undefined && next.startedAt === undefined ? { startedAt: started } : {}),
        ...(updated !== undefined ? { updatedAt: updated } : {}),
      };
      if (s === 'cancelled' || s === 'stopped') return { ...timed, status: 'cancelled' };
      // A snapshot says the run started, never that it went back to the queue: a status only moves forward.
      if (s === 'running' && next.status === 'queued') return { ...timed, status: 'running' };
      return timed;
    }
    case 'CUSTOM': {
      const value = (raw.value ?? {}) as Record<string, unknown>;
      switch (str(raw.name)) {
        case 'engine_progress':
          return { ...next, status: state.status === 'queued' ? 'running' : state.status, frames: [...state.frames, value as unknown as SSEEngineProgressEvent] };
        case 'approval_requested': {
          const interactionId = str(value.interactionId) ?? str(value.toolCallId);
          if (!interactionId || state.asks.some((a) => a.interactionId === interactionId)) return next;
          const ask: JobAsk = {
            interactionId,
            toolName: str(value.toolName) ?? 'tool',
            ...(str(value.displayName) ? { displayName: str(value.displayName) } : {}),
            argsSummary: str(value.argsSummary) ?? null,
            status: 'pending',
          };
          return { ...next, status: 'suspended', asks: [...state.asks, ask] };
        }
        case 'approval_resolved': {
          const interactionId = str(value.interactionId);
          const asks = state.asks.map((a) => (a.interactionId === interactionId ? { ...a, status: settled(str(value.decision)) } : a));
          const open = asks.some((a) => a.status === 'pending');
          return { ...next, asks, status: state.status === 'suspended' && !open ? 'running' : state.status };
        }
        default:
          return next;
      }
    }
    default:
      return next;
  }
}

/** An ask still open when its run ended was never answered: it did not go ahead. */
const closeOpen = (asks: ReadonlyArray<JobAsk>): JobAsk[] => asks.map((a) => (a.status === 'pending' ? { ...a, status: 'closed' } : a));

export function jobStreamFrom(runId: string, frames: Iterable<unknown>): JobStreamState {
  let state = emptyJobStream(runId);
  for (const f of frames) state = reduceJobFrame(state, f);
  return state;
}

/** The job's frames are the root of their own stream: its last frame names it, not a parent. */
export function endsJobStream(input: unknown, runId: string): boolean {
  const frame = frameOf(input);
  if (!frame || (frame.type !== 'RUN_FINISHED' && frame.type !== 'RUN_ERROR')) return false;
  const named = str((frame as unknown as Record<string, unknown>).runId);
  return !named || named === runId;
}
