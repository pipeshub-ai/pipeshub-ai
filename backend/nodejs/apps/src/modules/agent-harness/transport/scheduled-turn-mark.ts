import type { Request } from 'express';
import type { ScheduledTurnMark } from '../application/classic-schedule-runner';

/** Sent to the query service on a schedule's classic turn (ADR-SCH-02 D4): a person reads it later. */
export const SCHEDULE_FIRING_HEADER = 'x-pipeshub-schedule-firing';
/** Until when an ask in that turn waits for the person: an ISO instant, the firing's deadline. */
export const ASK_DEADLINE_HEADER = 'x-pipeshub-ask-deadline';

/** The headers only the scheduled classic runner sets; a client's own are dropped before a request is forwarded. */
export const SCHEDULED_TURN_HEADERS: readonly string[] = [
  SCHEDULE_FIRING_HEADER,
  ASK_DEADLINE_HEADER,
];

// Keyed by the request object the runner made, so nothing a client sends can mark a turn as scheduled.
const marks = new WeakMap<Request, ScheduledTurnMark>();

export const markScheduledTurn = (
  req: Request,
  mark: ScheduledTurnMark,
): void => {
  marks.set(req, mark);
};

export const scheduledTurnOf = (req: Request): ScheduledTurnMark | null =>
  marks.get(req) ?? null;

/** The query service's headers for a scheduled turn; none for any other request. */
export function scheduledTurnHeaders(req: Request): Record<string, string> {
  const mark = scheduledTurnOf(req);
  if (mark === null) return {};
  return {
    [SCHEDULE_FIRING_HEADER]: mark.firingId,
    [ASK_DEADLINE_HEADER]: new Date(mark.deadlineAt).toISOString(),
  };
}

/**
 * How long a scheduled turn's stream runs with no browser before it is stopped (`attachUpstreamAbort`'s cap): no
 * one watches it, so it is detached from the start and stopped at its firing's deadline, its partial answer saved as
 * a person's leaving saves it. Until then the firing counts it running, so the next firing waits (overlap). Null for
 * any other request, which keeps the usual cap from its browser's leaving.
 */
export function scheduledTurnDetachMs(
  req: Request,
  now: number = Date.now(),
): number | null {
  const mark = scheduledTurnOf(req);
  return mark === null ? null : Math.max(1, mark.deadlineAt - now);
}

export interface ScheduledConversationOrigin {
  kind: 'schedule';
  scheduleId: string;
  firingId: string;
  /** The schedule's name when it fired, for the conversation's header. */
  scheduleName: string;
}

/** The origin a scheduled turn's conversation records (`metadata.origin`), so a person sees which schedule made it. */
export function scheduledTurnOrigin(
  mark: ScheduledTurnMark,
): ScheduledConversationOrigin {
  return {
    kind: 'schedule',
    scheduleId: mark.scheduleId,
    firingId: mark.firingId,
    scheduleName: mark.title,
  };
}

/** A conversation's schedule origin, from its stored metadata (a Map from Mongo, an object from PostgreSQL). */
export function scheduledConversationOrigin(
  metadata: Map<string, unknown> | Record<string, unknown> | undefined,
): ScheduledConversationOrigin | null {
  const origin: unknown =
    metadata instanceof Map ? metadata.get('origin') : metadata?.['origin'];
  if (typeof origin !== 'object' || origin === null) return null;
  const o = origin as Record<string, unknown>;
  if (
    o['kind'] !== 'schedule' ||
    typeof o['scheduleId'] !== 'string' ||
    typeof o['firingId'] !== 'string'
  ) {
    return null;
  }
  return {
    kind: 'schedule',
    scheduleId: o['scheduleId'],
    firingId: o['firingId'],
    scheduleName:
      typeof o['scheduleName'] === 'string' ? o['scheduleName'] : '',
  };
}
