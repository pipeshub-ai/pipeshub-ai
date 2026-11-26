/**
 * `next_run` (SS2, F11 3.7): a message a participant sends while someone
 * else's run is active never joins that run, whose grant and credentials are
 * the other person's. It is kept as the admission it was decided to be, and
 * admitted as its sender when the active run ends.
 */
import type { AdmitRunCommand, QueuedNextRun } from './admission';
import type { HarnessRun } from './execution';

/** A message waiting for the session's active run to end. */
export interface NextRunRecord {
  id: string;
  orgId: string;
  sessionId: string;
  actorId: string;
  /** Chosen at enqueue, so the sender can follow the run it becomes. */
  runId: string;
  /** The admission as decided when the message came, with its deadline on DB time. */
  command: AdmitRunCommand;
  /** The run it waited behind when it was queued. */
  behindRunId: string;
  createdAt: number;
}

export type NextRunOutcome =
  { status: 'admitted'; runId: string } | { status: 'refused'; reason: string };

/** Where next runs wait; both admission stores implement it. */
export interface NextRunQueue {
  /** The session's oldest pending next run, or null. */
  nextPending(orgId: string, sessionId: string): Promise<NextRunRecord | null>;
  /** Sessions with a pending next run and no active run, oldest first: what a drain missed. */
  drainable(
    limit: number,
  ): Promise<Array<{ orgId: string; sessionId: string }>>;
  /** Settles a pending next run; a settled one is left as it is. */
  settleNextRun(
    orgId: string,
    id: string,
    outcome: NextRunOutcome,
    now: number,
  ): Promise<void>;
}

/** Erasure drops a session's waiting messages before anything can drain them (E7). */
export interface NextRunEraser {
  /** Drops what waits for the session's next run: its queued messages, and the person's `/compact` (SLASH2 C2). */
  dropNextRuns(orgId: string, sessionId: string): Promise<number>;
}

/** Whether a message to a session whose active run is `active` waits as a next run. */
export const waitsAsNextRun = (
  active: Pick<HarnessRun, 'actorId'>,
  command: Pick<AdmitRunCommand, 'actorId' | 'nextRunId'>,
): boolean =>
  command.nextRunId === undefined && active.actorId !== command.actorId;

export const nextRunIdempotencyKey = (id: string): string => `next-run:${id}`;

/**
 * The admission that drains `record`: its own idempotency key (a second
 * drain replays it), the run id chosen at enqueue, never a steer.
 */
export function drainCommand(record: NextRunRecord): AdmitRunCommand {
  // A label decision is never carried to a later admission (P2.5a): the drain
  // has no sender credential to decide again, so it lists no visible run.
  const {
    steerOnly: _steer,
    visibleRuns: _visible,
    ...command
  } = record.command;
  return {
    ...structuredClone(command),
    idempotencyKey: nextRunIdempotencyKey(record.id),
    runId: record.runId,
    dispatchCommandId: `dispatch:${nextRunIdempotencyKey(record.id)}`,
    nextRunId: record.id,
  };
}

/** What a queued message's sender is told; `run` is the active run, without its input. */
export function queuedNextRun(
  record: Pick<NextRunRecord, 'id' | 'runId'>,
  position: number,
): QueuedNextRun {
  return { id: record.id, runId: record.runId, position };
}

/** The active run as a next run's sender may see it in an admission result. */
export function withoutInput(run: HarnessRun): HarnessRun {
  const { input: _input, ...rest } = run;
  return structuredClone(rest);
}
