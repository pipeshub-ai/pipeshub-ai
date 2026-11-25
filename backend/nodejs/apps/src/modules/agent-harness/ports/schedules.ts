import type { RunInputSnapshot, RunStatus } from '../domain/execution';
import type {
  AgentSchedule,
  AgentVersionPin,
  EventTriggerKind,
  FiringOutcome,
  NotifyChannel,
  RunTrigger,
  RunTriggerContext,
  ScheduleFiring,
  ScheduleStatus,
} from '../domain/schedules';
import type { RunDigest } from '../projection/run-digest';

export interface ScheduleLimits {
  perActor: number;
  perOrg: number;
}

/** Where schedules and their firings live (P8a); both adapters pass `schedule-store-contract`. */
export interface ScheduleStore {
  /** Store time: every replica decides due slots and leases on one clock. */
  now(): Promise<number>;
  /** Refuses `SCHEDULE_LIMIT` when the actor or the org holds as many as allowed. */
  create(
    schedule: AgentSchedule,
    limits: ScheduleLimits,
  ): Promise<AgentSchedule>;
  /** `orgId` null only for a webhook, whose token is checked next. */
  get(orgId: string | null, id: string): Promise<AgentSchedule | null>;
  /** Newest first; `actorId` absent lists the org's. */
  list(
    orgId: string,
    filter: { actorId?: string },
    limit: number,
  ): Promise<AgentSchedule[]>;
  /**
   * Moves a schedule whose status is one of `from` to `to`, with the cursor
   * given; null when it is gone or in another status.
   */
  setStatus(
    orgId: string,
    id: string,
    change: {
      from: readonly ScheduleStatus[];
      to: ScheduleStatus;
      reason: string | null;
      nextFireAt: number | null;
    },
    now: number,
  ): Promise<AgentSchedule | null>;
  /** Moves an agent schedule's version pin (OWD-14); null when it is gone, disabled or not an agent's. */
  setAgentVersion(
    orgId: string,
    id: string,
    agentVersion: AgentVersionPin,
    now: number,
  ): Promise<AgentSchedule | null>;
  /** Deletes the schedule and its firings; false when it was not there. */
  delete(orgId: string, id: string): Promise<boolean>;
  /** Active cron schedules whose cursor is due, oldest cursor first. */
  dueCron(now: number, limit: number): Promise<AgentSchedule[]>;
  /**
   * Fires one cron slot: inserts `firing` and moves the schedule's cursor
   * from `expectedNextFireAt` to `nextFireAt`, together. Null (nothing
   * written) when another scheduler already moved the cursor or claimed the slot.
   */
  claimCronSlot(
    firing: ScheduleFiring,
    expectedNextFireAt: number,
    nextFireAt: number | null,
  ): Promise<ScheduleFiring | null>;
  /** Claims a manual or event slot: the slot's existing firing when it was claimed before. */
  claimSlot(
    firing: ScheduleFiring,
  ): Promise<{ firing: ScheduleFiring; created: boolean }>;
  /** Active schedules of an event trigger kind in the org (for a connector, of that connector). */
  eventSchedules(
    orgId: string,
    kind: EventTriggerKind,
    connectorId?: string,
  ): Promise<AgentSchedule[]>;
  /** Whether any active schedule has a trigger of this kind (the broker source starts only then). */
  hasActive(kind: EventTriggerKind): Promise<boolean>;
  /** Settles a pending firing; a settled one is left as it is (false). */
  settle(
    orgId: string,
    firingId: string,
    outcome: FiringOutcome,
    now: number,
  ): Promise<boolean>;
  /** A pending firing whose attempt failed transiently: retaken after `leaseUntil`. */
  release(
    orgId: string,
    firingId: string,
    attempts: number,
    leaseUntil: number,
  ): Promise<void>;
  /** Pending firings whose lease passed, each retaken once (attempts + 1, a new lease). */
  takeStale(
    now: number,
    leaseMs: number,
    limit: number,
  ): Promise<ScheduleFiring[]>;
  /** A schedule's firings, newest first. */
  firings(
    orgId: string,
    scheduleId: string,
    limit: number,
  ): Promise<ScheduleFiring[]>;
  getFiring(orgId: string, firingId: string): Promise<ScheduleFiring | null>;
  /** The firing of one slot (a scheduled run's idempotency key names it). */
  firingBySlot(
    orgId: string,
    scheduleId: string,
    slotKey: string,
  ): Promise<ScheduleFiring | null>;
  /**
   * Admitted firings whose run is still watched: `watchUntil` after
   * `now - WATCH_GRACE_MS`, so a run expired at its deadline is still seen
   * ending. Those checked longest ago first, so no firing is starved.
   */
  watched(now: number, limit: number): Promise<ScheduleFiring[]>;
  /** Records that the watch looked at a firing now (its place in the round). */
  markChecked(orgId: string, firingId: string, now: number): Promise<void>;
  markNotified(orgId: string, firingId: string, now: number): Promise<boolean>;
  /** Ends a firing's watch; true only for the call that ended it (its run's end is told once). */
  unwatch(orgId: string, firingId: string, now: number): Promise<boolean>;
  /** A schedule's admitted firings whose run may still be active (watched), newest first (TR-06). */
  activeFirings(
    orgId: string,
    scheduleId: string,
    limit: number,
  ): Promise<ScheduleFiring[]>;
  /** A schedule's pending firing queued behind an active run, if any (TR-06: one at most). */
  queuedFiring(
    orgId: string,
    scheduleId: string,
  ): Promise<ScheduleFiring | null>;
  /**
   * Puts a pending firing back to wait behind `runId` until `leaseUntil`
   * (reason `overlap_queued`, which its history shows),
   * giving back the attempt its retake counted (waiting is not a failed
   * attempt). False when it is no longer pending at `attempts`.
   */
  requeue(
    orgId: string,
    firingId: string,
    attempts: number,
    runId: string,
    leaseUntil: number,
  ): Promise<boolean>;
  /**
   * Fixes a firing's trigger context at its first attempt; a retry gets the
   * one stored, so every attempt admits the same input (TR-03).
   */
  fixContext(
    orgId: string,
    firingId: string,
    context: RunTriggerContext,
  ): Promise<RunTriggerContext>;
  /**
   * Counts one authenticated webhook delivery in the schedule's window
   * starting `windowStart` and answers the window's count, across replicas
   * (TR-04). The count is kept until `expiresAt`.
   */
  countDelivery(
    orgId: string,
    scheduleId: string,
    windowStart: number,
    expiresAt: number,
  ): Promise<number>;
  /**
   * Records one signed webhook delivery by its nonce and answers whether it
   * is the first, across replicas (SEC-C-13). The nonce is kept until
   * `expiresAt`, past which the signature's timestamp is refused anyway.
   */
  firstDelivery(
    orgId: string,
    scheduleId: string,
    nonce: string,
    at: number,
    expiresAt: number,
  ): Promise<boolean>;
}

/** What user and org deletion call (C-14 principal half): nothing runs as an erased owner. */
export const SCHEDULE_ERASER = 'AgentScheduleEraser';

export interface ScheduleEraser {
  /** Deletes the user's schedules, their firings and their count (CLEANUP-G2). */
  eraseUser(orgId: string, userId: string): Promise<number>;
  eraseOrg(orgId: string): Promise<number>;
  /** Disables an agent's schedules and drops their templates and webhook secrets, once the agent is deleted (H2). */
  eraseAgent(orgId: string, agentKey: string): Promise<number>;
  /**
   * SEC-AZ-08: pauses the user's active schedules and triggers with `reason`, as a firing refused for a
   * disabled actor does; paused and disabled ones are left as they are. Only their owner resumes them.
   */
  pauseUser(orgId: string, userId: string, reason: string): Promise<number>;
}

export type ActorRefusal =
  'actor_deleted' | 'actor_disabled' | 'actor_not_person' | 'org_inactive';

/**
 * Who a scheduled run acts as, read at each firing: the actor as they are
 * now, and a short-lived credential of theirs for the agent authority.
 * Rejects when the decision cannot be made (an outage): the firing retries.
 */
export interface ActorAuthority {
  authorize(
    orgId: string,
    actorId: string,
  ): Promise<{ authorization: string } | { refused: ActorRefusal }>;
  /** Whether the user is an org admin (an admin may trigger runs from connector events). */
  isOrgAdmin(orgId: string, userId: string): Promise<boolean>;
}

/** Who may list and act on (pause, resume, delete, repin) schedules other people own; never run them as the owner. */
export interface ScheduleReach {
  reachesOthers(orgId: string, userId: string): Promise<boolean>;
}

/**
 * Which published version of an agent an actor would run (OWD-14), asked of
 * the agent authority with the actor's own credential: `requested` absent,
 * the latest. Null when they may not run it or there is no such version;
 * rejects when it cannot be decided.
 */
export interface AgentVersionResolver {
  resolve(
    orgId: string,
    actorId: string,
    agentKey: string,
    requested?: number,
  ): Promise<number | null>;
}

/** Labs `ENABLE_SCHEDULED_RUNS` (org). Unreadable reads as off. */
export interface ScheduledRunsSetting {
  enabled(orgId: string): Promise<boolean>;
}

export interface ScheduledAdmission {
  orgId: string;
  actorId: string;
  agentKey?: string;
  /** The agent version the schedule pinned (OWD-14); absent: the latest published. */
  agentVersion?: number;
  idempotencyKey: string;
  deadlineMs: number;
  input: RunInputSnapshot;
  authorization: string;
  trigger: RunTrigger;
}

export type ScheduledAdmissionOutcome =
  | { kind: 'admitted'; runId: string; sessionId: string }
  | { kind: 'refused'; reason: string };

/** Admits a firing's run through the ordinary admission path (`RunAdmissionService` in production). */
export interface ScheduledRunAdmission {
  admit(request: ScheduledAdmission): Promise<ScheduledAdmissionOutcome>;
}

/** A fired run's status, for the approval watch. */
export interface RunStatusReader {
  status(orgId: string, runId: string): Promise<RunStatus | null>;
}

/**
 * AP-10: what a bound feature sends a scheduled run's actor besides the Inbox notice when the run starts waiting on
 * them, on the outside channels the schedule and the org allow. Unbound: the Inbox notice only.
 */
export interface ScheduledWaitNotices {
  runWaiting(notice: {
    orgId: string;
    actorId: string;
    scheduleName: string;
    agentKey?: string;
    runId: string;
    channels: readonly ('email' | 'slack')[];
  }): Promise<void>;
}

/** Tells a schedule's actor what needs them: an approval its run waits on, or a firing that could not run. */
export interface ScheduleNotifier {
  approvalWaiting(notice: {
    orgId: string;
    actorId: string;
    scheduleId: string;
    scheduleName: string;
    /** The agent the run is a conversation of; the link opens that agent's chat. */
    agentKey?: string;
    sessionId: string;
    runId: string;
    deadlineAt: number;
  }): Promise<void>;
  /** A firing was refused (UX3, R1 F14): at most once a day per schedule, so a broken schedule is not silent. */
  firingRefused(notice: {
    orgId: string;
    actorId: string;
    scheduleId: string;
    scheduleName: string;
    reason: string;
    /** G2: the actor paused their notices; stored in the Inbox, not pushed. */
    quiet?: boolean;
  }): Promise<void>;
  /** A fired run ended (TR-07), on the channels the schedule and the org allow; told once per run. */
  runEnded(notice: RunEndedNotice): Promise<void>;
}

export interface RunEndedNotice {
  orgId: string;
  actorId: string;
  scheduleId: string;
  scheduleName: string;
  agentKey?: string;
  sessionId: string;
  runId: string;
  status: 'completed' | 'failed' | 'cancelled';
  channels: NotifyChannel[];
  /** What the run did (G-07), folded from its journal; absent when it could not be read. In the app only. */
  digest?: RunDigest;
  /** G2: the actor paused their notices; stored in the Inbox, not pushed. */
  quiet?: boolean;
}

/** A terminated run's end-of-run summary (G-07), for a reader the caller already authorized. */
export interface RunDigestReader {
  digest(
    orgId: string,
    runId: string,
    status: 'completed' | 'failed' | 'cancelled',
  ): Promise<RunDigest>;
}

/**
 * The org's trigger settings (TR-04, TR-07), as the policy store's deployment
 * and org layers narrow the release's: `triggers.*` dimensions.
 */
export interface TriggerSettings {
  /** The largest webhook body the org accepts, in bytes. */
  webhookMaxBytes(orgId: string): Promise<number>;
  /** Authenticated deliveries one webhook schedule takes per minute. */
  webhookPerMinute(orgId: string): Promise<number>;
  /** The channels a schedule's completion notice may use. */
  notifyChannels(orgId: string): Promise<readonly NotifyChannel[]>;
}

/**
 * Whether an agent has its own Slack bot (TR-07): a Slack notice reaches the
 * actor only from the schedule's agent's bot, so without one Slack says nothing.
 */
export interface AgentSlackBots {
  hasBot(agentKey: string): Promise<boolean>;
}

/** Why a notice channel cannot be picked: the org turned it off, or (Slack) the agent has no bot. */
export type NoticeChannelBlock = 'org_off' | 'no_slack_bot';

/** A notice channel as the schedule form offers it. */
export interface NoticeChannelChoice {
  channel: NotifyChannel;
  available: boolean;
  reason: NoticeChannelBlock | null;
}
