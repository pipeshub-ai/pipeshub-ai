import type { RunInputSnapshot } from '../domain/execution';
import type { RunTrigger, ScheduleRunnerKind } from '../domain/schedules';
import type { RunDigest } from '../projection/run-digest';

/** Where a firing's run is, as the firer watches it (ADR-SCH-02 D2), whichever runner started it. */
export type ScheduleRunStatus =
  | 'queued'
  | 'running'
  | 'waiting_for_you'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type EndedScheduleRunStatus = Extract<
  ScheduleRunStatus,
  'completed' | 'failed' | 'cancelled'
>;

/** A run's status and, for one that failed, why (`ask_expired`: an approval nobody gave by the deadline). */
export interface ScheduleRunState {
  status: ScheduleRunStatus;
  reason?: string;
}

/** What a firing hands its runner: the schedule's run as its actor, with the actor's credential minted for it. */
export interface ScheduleRunRequest {
  orgId: string;
  actorId: string;
  scheduleId: string;
  scheduleName: string;
  firingId: string;
  agentKey?: string;
  /** The agent version the schedule pinned (OWD-14); absent: the latest published. */
  agentVersion?: number;
  /** The coding engine the schedule keeps for its runs' new chats (an engine kind or `none`); absent: the default. */
  codingEngine?: string;
  idempotencyKey: string;
  deadlineMs: number;
  /** When the run's watch ends: an ask still open then expires. */
  deadlineAt: number;
  input: RunInputSnapshot;
  authorization: string;
  trigger: RunTrigger;
  /** Told once when the run ends in this process, so its notice need not wait for the next watch. */
  ended?: () => void;
}

export type ScheduleRunStart =
  | {
      kind: 'started';
      runRef: string;
      /** The conversation the run answers in (the notice's link). */
      sessionId: string;
      /** The durable run's id; absent for a classic turn. */
      runId?: string;
      /** The schedule's cost cap does not apply to this run (D6). */
      costCapIgnored?: true;
    }
  | { kind: 'refused'; reason: string };

/**
 * Runs a schedule's firing (ADR-SCH-02 D2). The firer picks one per firing and records its kind and `runRef`, so the
 * same runner is asked about the run until it ends, whichever path the org is on by then.
 */
export interface ScheduleRunner {
  readonly kind: ScheduleRunnerKind;
  start(request: ScheduleRunRequest): Promise<ScheduleRunStart>;
  /** Null when the run cannot be found (gone, erased). `deadlineAt`: the firing's watch end. */
  status(
    orgId: string,
    runRef: string,
    deadlineAt: number | null,
  ): Promise<ScheduleRunState | null>;
  /** What the run did, for its notice; null when it cannot be read. */
  summary(
    orgId: string,
    runRef: string,
    status: EndedScheduleRunStatus,
  ): Promise<RunDigest | null>;
}
