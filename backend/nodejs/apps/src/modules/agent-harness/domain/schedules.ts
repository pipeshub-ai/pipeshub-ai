/**
 * Scheduled and triggered agent runs (P8a, REPORT 6.12): a schedule admits a
 * durable run of a named agent with a fixed input, on behalf of the user who
 * created it, at every slot of its cron or at every window of its trigger.
 * Each slot fires at most once: the firing's unique key per (schedule, slot)
 * and admission's own receipt per idempotency key.
 */
import { createHmac, timingSafeEqual } from 'node:crypto';
import { type CronExpression, parseExpression } from 'cron-parser';
import { ERROR_CODES } from '../contracts/generated/ext/pipeshub/errors';
import { HarnessScheduleRefusedError } from './errors';
import type { RunInputSnapshot } from './execution';
import { RUN_SPEC_BUDGETS } from './run-spec';

/**
 * What a tick does with slots the scheduler missed while it was down (TR-02):
 * `skip` them, `run_latest` (one run, for the latest), or `run_all` (one run
 * per missed slot, oldest first, one slot per tick and at most
 * `MAX_CATCH_UP_RUNS` of them).
 */
export type CatchUpPolicy = 'skip' | 'run_latest' | 'run_all';

/**
 * What a firing does while the schedule's previous run is still active
 * (TR-06): `skip` the slot, `queue` it until that run ends (one queued slot at
 * most; a later one is skipped), or `allow` both to run.
 */
export type OverlapPolicy = 'allow' | 'skip' | 'queue';

/**
 * How a webhook caller proves it holds the schedule's secret (TR-04):
 * `signature` an HMAC-SHA256 of the timestamp and the body, `token` the
 * secret itself in a header (schedules made before signatures).
 */
export type WebhookAuth = 'signature' | 'token';

export type ScheduleTrigger =
  | { kind: 'cron'; cron: string; timeZone: string; catchUp: CatchUpPolicy }
  | { kind: 'webhook'; coalesceS: number; auth?: WebhookAuth }
  | { kind: 'connector'; connectorId: string; coalesceS: number };

/** Where a schedule's actor hears that a run finished (TR-07). */
export type NotifyChannel = 'in_app' | 'email' | 'slack';
export const NOTIFY_CHANNELS: readonly NotifyChannel[] = [
  'in_app',
  'email',
  'slack',
];

export type EventTriggerKind = Exclude<ScheduleTrigger['kind'], 'cron'>;

export type ScheduleStatus = 'active' | 'paused' | 'disabled';

/** A trigger event as a source reports it: never its payload (REPORT 6.12: `external` data). */
export interface TriggerEvent {
  orgId: string;
  kind: EventTriggerKind;
  connectorId?: string;
  /** The connector record the event announces: a reference the run may read only if its actor can. */
  recordId?: string;
  /** A webhook body's size, digest and type; the body itself never reaches the run. */
  webhook?: { bodyBytes: number; bodySha256: string; contentType?: string };
  at: number;
}

/**
 * Why a run exists, as its input carries it to the worker, which stages it at
 * `/pipeshub/inputs/trigger.json` (TR-03): ids and times only. A connector
 * record is named only when the actor may read it; its content reaches the
 * run through the run's own tools, labelled and access-checked there.
 */
export interface RunTriggerContext {
  kind: 'cron' | 'manual' | EventTriggerKind;
  scheduleId: string;
  firingId: string;
  slotAt: string;
  /** An event trigger's coalescing window: every event in it is this one run. */
  window?: { start: string; end: string };
  event?: {
    connectorId: string;
    eventType: 'newRecord';
    at: string;
    recordId?: string;
  };
  webhook?: {
    receivedAt: string;
    bodyBytes: number;
    bodySha256: string;
    contentType?: string;
  };
}

/**
 * Which published version of its agent a schedule runs (OWD-14): a number it
 * keeps until its owner moves it, or `latest` when the owner opted in to
 * follow every publish.
 */
export type AgentVersionPin = number | 'latest';

/** The version GA-01's migration made of every agent as it stood: what a schedule from before versions runs. */
export const MIGRATED_AGENT_VERSION = 1;

export interface AgentSchedule {
  id: string;
  orgId: string;
  /** Who every run acts as: the creator, re-authorized at each firing. */
  actorId: string;
  name: string;
  /** Absent: the built-in assistant. */
  agentKey?: string;
  /** Absent on an agent schedule made before versions: it reads as `MIGRATED_AGENT_VERSION`. */
  agentVersion?: AgentVersionPin;
  /** Null once the actor was deleted (the template is the actor's content). */
  input: RunInputSnapshot | null;
  trigger: ScheduleTrigger;
  /** Each firing's own cap, at most the per-run ceiling; absent: the ceiling. */
  maxCostUsd?: number;
  /** A firing's run deadline: an unanswered approval expires with it. */
  deadlineS: number;
  status: ScheduleStatus;
  /** Why it is paused or disabled: a reason code, or `by_actor` / `by_admin`. */
  statusReason: string | null;
  /** The next cron slot not yet fired; null for event triggers and while disabled. */
  nextFireAt: number | null;
  /** SHA-256 of a webhook trigger's secret; the secret itself is shown once. */
  webhookTokenHash: string | null;
  /**
   * A signed webhook's HMAC key (TR-04): the store seals it with the org's
   * data key, since verifying a signature needs the key itself.
   */
  webhookSigningKey?: string | null;
  /** Absent: `DEFAULT_OVERLAP`. */
  overlap?: OverlapPolicy;
  /** Absent: `DEFAULT_NOTIFY`. */
  notify?: NotifyChannel[];
  createdAt: number;
  updatedAt: number;
}

export type FiringKind = 'cron' | 'manual' | 'event';
export type FiringStatus = 'pending' | 'admitted' | 'refused' | 'skipped';

export interface ScheduleFiring {
  id: string;
  orgId: string;
  scheduleId: string;
  actorId: string;
  slotKey: string;
  slotAt: number;
  kind: FiringKind;
  status: FiringStatus;
  runId: string | null;
  sessionId: string | null;
  reason: string | null;
  /** Slots that passed unfired before this one (catch-up). */
  missed: number;
  attempts: number;
  /** A pending firing's lease: after it, any scheduler may take it again. */
  leaseUntil: number;
  createdAt: number;
  settledAt: number | null;
  /** Until when its run is watched for an approval nobody can answer at night. */
  watchUntil: number | null;
  notifiedAt: number | null;
  /** The event that opened an event firing's window (TR-03); absent for cron and manual. */
  event?: FiringEvent;
  /** The run's trigger context, fixed at its first attempt so every retry admits the same input. */
  context?: RunTriggerContext;
  /** A queued firing waits for this run to end (TR-06). */
  queuedBehind?: string;
  /** When the watch saw its run end and told its actor (TR-07): once per run. */
  watchEndedAt?: number;
}

/** What a firing keeps of its event: references and sizes, never content. */
export interface FiringEvent {
  connectorId?: string;
  recordId?: string;
  webhook?: { bodyBytes: number; bodySha256: string; contentType?: string };
  at: number;
}

export type FiringOutcome =
  | { status: 'admitted'; runId: string; sessionId: string; watchUntil: number }
  | { status: 'refused'; reason: string }
  | { status: 'skipped'; reason: string };

export const SCHEDULE_TICK_MS = 15_000;
/** A slot this late is a missed one, which the catch-up policy decides. */
export const MISFIRE_GRACE_MS = 5 * 60_000;
export const MIN_CRON_INTERVAL_MS = 5 * 60_000;
/** Slots looked at when checking a cron's shortest gap. */
const INTERVAL_PROBE_SLOTS = 50;
/** Missed slots counted after an outage; more are reported as this many. */
const MAX_MISSED_COUNTED = 1_000;
export const FIRING_LEASE_MS = 60_000;
export const FIRING_MAX_ATTEMPTS = 5;
export const FIRING_BACKOFF_MAX_MS = 10 * 60_000;
export const MAX_SCHEDULES_PER_ACTOR = 50;
export const MAX_SCHEDULES_PER_ORG = 500;
export const DEFAULT_DEADLINE_S = 8 * 60 * 60;
export const MIN_DEADLINE_S = 5 * 60;
export const MAX_DEADLINE_S = 24 * 60 * 60;
export const DEFAULT_COALESCE_S = 300;
export const MIN_COALESCE_S = 60;
export const MAX_COALESCE_S = 24 * 60 * 60;
export const MAX_SCHEDULE_QUERY_CHARS = 10_000;
/** The actor credential minted at a firing only asks the agent authority, once. */
export const ACTOR_CREDENTIAL_TTL = '5m';
export const MAX_DUE_PER_TICK = 50;
export const MAX_WATCHED_PER_TICK = 50;
/** `run_all` replays at most this many missed slots; older ones are counted as missed (TR-02). */
export const MAX_CATCH_UP_RUNS = 24;
/** A firing queued behind an active run looks again this often (TR-06). */
export const OVERLAP_RECHECK_MS = 30_000;
/** A run that ends at its deadline is still seen ending: the watch outlives the deadline by this much (TR-07). */
export const WATCH_GRACE_MS = 15 * 60_000;
export const DEFAULT_OVERLAP: OverlapPolicy = 'skip';
export const DEFAULT_NOTIFY: readonly NotifyChannel[] = ['in_app'];
/**
 * A webhook signature's timestamp may be this far from Node's clock. Inside it a
 * delivery is accepted once (SEC-C-13); the window bounds how long that memory is kept.
 */
export const WEBHOOK_SIGNATURE_TOLERANCE_S = 120;
/** Events older than this are not windowed by their own time: a redelivery that late is a new event. */
export const MAX_EVENT_AGE_MS = MAX_COALESCE_S * 1000;

const invalid = (field: string, message: string): HarnessScheduleRefusedError =>
  new HarnessScheduleRefusedError('SCHEDULE_INVALID', message, field);

export function assertTimeZone(timeZone: string): void {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone });
  } catch {
    throw invalid('timeZone', 'Unknown time zone');
  }
}

function cronIterator(
  cron: string,
  timeZone: string,
  at: number,
): CronExpression {
  return parseExpression(cron, { currentDate: new Date(at), tz: timeZone });
}

/** The first slot strictly after `after`, or null when the cron never fires again. */
export function nextCronSlot(
  cron: string,
  timeZone: string,
  after: number,
): number | null {
  try {
    return cronIterator(cron, timeZone, after).next().getTime();
  } catch {
    return null;
  }
}

/** The latest slot at or before `at`, or null. */
export function latestCronSlot(
  cron: string,
  timeZone: string,
  at: number,
): number | null {
  try {
    return cronIterator(cron, timeZone, at + 1)
      .prev()
      .getTime();
  } catch {
    return null;
  }
}

/**
 * Refuses a cron that does not parse, names an unknown zone, never fires, or
 * fires more often than every five minutes (a run per slot costs money).
 */
export function assertCron(cron: string, timeZone: string, now: number): void {
  assertTimeZone(timeZone);
  if (cron.trim().split(/\s+/).length !== 5) {
    throw invalid('cron', 'A cron expression has five fields');
  }
  let slots: number[];
  try {
    const iterator = cronIterator(cron, timeZone, now);
    slots = [];
    for (let i = 0; i < INTERVAL_PROBE_SLOTS; i += 1) {
      try {
        slots.push(iterator.next().getTime());
      } catch {
        break;
      }
    }
  } catch {
    throw invalid('cron', 'The cron expression does not parse');
  }
  if (slots.length === 0) {
    throw invalid('cron', 'The cron expression never fires');
  }
  for (let i = 1; i < slots.length; i += 1) {
    if (
      (slots[i] as number) - (slots[i - 1] as number) <
      MIN_CRON_INTERVAL_MS
    ) {
      throw invalid('cron', 'A schedule may fire at most every five minutes');
    }
  }
}

export type CronDecision =
  | { kind: 'wait' }
  | {
      kind: 'fire' | 'skip';
      slotAt: number;
      /** The first slot after now: the cursor the claim moves to. */
      nextFireAt: number | null;
      /** Slots before `slotAt` that never fired. */
      missed: number;
      late: boolean;
    };

function countSlots(
  cron: string,
  timeZone: string,
  from: number,
  before: number,
): number {
  let count = 0;
  let at = from - 1;
  for (;;) {
    const next = nextCronSlot(cron, timeZone, at);
    if (next === null || next >= before || count >= MAX_MISSED_COUNTED) {
      return count;
    }
    count += 1;
    at = next;
  }
}

/** Up to `limit` slots at or after `from` and at or before `at`, newest first. */
function slotsBack(
  cron: string,
  timeZone: string,
  from: number,
  at: number,
  limit: number,
): number[] {
  const found: number[] = [];
  try {
    const iterator = cronIterator(cron, timeZone, at + 1);
    while (found.length < limit) {
      const slot = iterator.prev().getTime();
      if (slot < from) break;
      found.push(slot);
    }
  } catch {
    // No earlier slot.
  }
  return found;
}

/**
 * `run_all` (TR-02): the oldest due slot fires and the cursor moves only past
 * it, so each later tick fires the next missed slot: an outage replays as one
 * run per tick, not as a burst. Beyond the newest `MAX_CATCH_UP_RUNS` the
 * older slots are counted as missed and never fire.
 */
function decideRunAll(
  trigger: Extract<ScheduleTrigger, { kind: 'cron' }>,
  nextFireAt: number,
  now: number,
): CronDecision {
  const recent = slotsBack(
    trigger.cron,
    trigger.timeZone,
    nextFireAt,
    now,
    MAX_CATCH_UP_RUNS,
  );
  const slotAt = recent[recent.length - 1] ?? nextFireAt;
  const missed =
    slotAt > nextFireAt
      ? countSlots(trigger.cron, trigger.timeZone, nextFireAt, slotAt)
      : 0;
  return {
    kind: 'fire',
    slotAt,
    nextFireAt: nextCronSlot(trigger.cron, trigger.timeZone, slotAt),
    missed,
    late: now - slotAt > MISFIRE_GRACE_MS,
  };
}

/**
 * What a tick does with a cron schedule whose cursor is `nextFireAt` (the
 * oldest slot not fired yet): the latest due slot fires when on time; when
 * late (the scheduler was down) the catch-up policy decides. Under `skip` and
 * `run_latest` earlier missed slots never fire; `run_all` replays them one
 * per tick, at most `MAX_CATCH_UP_RUNS`.
 */
export function decideCron(
  trigger: Extract<ScheduleTrigger, { kind: 'cron' }>,
  nextFireAt: number,
  now: number,
): CronDecision {
  if (now < nextFireAt) return { kind: 'wait' };
  if (trigger.catchUp === 'run_all') {
    return decideRunAll(trigger, nextFireAt, now);
  }
  const latest = latestCronSlot(trigger.cron, trigger.timeZone, now);
  const slotAt = latest !== null && latest >= nextFireAt ? latest : nextFireAt;
  const missed =
    slotAt > nextFireAt
      ? countSlots(trigger.cron, trigger.timeZone, nextFireAt, slotAt)
      : 0;
  const late = now - slotAt > MISFIRE_GRACE_MS;
  return {
    kind: late && trigger.catchUp === 'skip' ? 'skip' : 'fire',
    slotAt,
    nextFireAt: nextCronSlot(trigger.cron, trigger.timeZone, now),
    missed,
    late,
  };
}

/** The window an event falls in: every event of one window is one firing. */
export function coalescedSlot(at: number, coalesceS: number): number {
  const windowMs = coalesceS * 1000;
  return Math.floor(at / windowMs) * windowMs;
}

export const cronSlotKey = (slotAt: number): string =>
  `c:${new Date(slotAt).toISOString()}`;
export const eventSlotKey = (slotAt: number): string =>
  `e:${new Date(slotAt).toISOString()}`;
export const manualSlotKey = (key: string): string => `manual:${key}`;

/** Admission's idempotency key for a slot: a second admission of it replays the first. */
export const scheduleIdempotencyKey = (
  scheduleId: string,
  slotKey: string,
): string => `schedule:${scheduleId}:${slotKey}`;

/** A schedule's agent version pin; null for the assistant, which has no versions. */
export function scheduleAgentVersion(
  schedule: Pick<AgentSchedule, 'agentKey' | 'agentVersion'>,
): AgentVersionPin | null {
  if (schedule.agentKey === undefined) return null;
  return schedule.agentVersion ?? MIGRATED_AGENT_VERSION;
}

/** The version a firing asks the agent authority for; undefined: its latest. */
export function firingAgentVersion(
  schedule: Pick<AgentSchedule, 'agentKey' | 'agentVersion'>,
): number | undefined {
  const pin = scheduleAgentVersion(schedule);
  return typeof pin === 'number' ? pin : undefined;
}

/**
 * The run input of one firing: the template, at the slot's time in the
 * schedule's zone, with why the run exists (TR-03).
 */
export function firingInput(
  schedule: Pick<AgentSchedule, 'input' | 'trigger'>,
  slotAt: number,
  context?: RunTriggerContext,
): RunInputSnapshot | null {
  if (schedule.input === null) return null;
  const timeZone =
    schedule.trigger.kind === 'cron'
      ? schedule.trigger.timeZone
      : schedule.input.timezone;
  return {
    ...structuredClone(schedule.input),
    ...(timeZone !== undefined ? { timezone: timeZone } : {}),
    currentTime: new Date(slotAt).toISOString(),
    ...(context !== undefined ? { trigger: structuredClone(context) } : {}),
  };
}

const iso = (at: number): string => new Date(at).toISOString();

/**
 * A firing's trigger context (TR-03). `recordReadable` says whether the actor
 * may read the record the event announced; a record they may not read is left
 * out, so the run never learns it exists.
 */
export function triggerContext(
  schedule: Pick<AgentSchedule, 'id' | 'trigger'>,
  firing: Pick<ScheduleFiring, 'id' | 'kind' | 'slotAt' | 'event'>,
  recordReadable: boolean,
): RunTriggerContext {
  const base = {
    scheduleId: schedule.id,
    firingId: firing.id,
    slotAt: iso(firing.slotAt),
  };
  const trigger = schedule.trigger;
  if (firing.kind !== 'event' || trigger.kind === 'cron') {
    return { kind: firing.kind === 'manual' ? 'manual' : 'cron', ...base };
  }
  const window = {
    start: iso(firing.slotAt),
    end: iso(firing.slotAt + trigger.coalesceS * 1000),
  };
  const event = firing.event;
  if (trigger.kind === 'connector') {
    return {
      kind: 'connector',
      ...base,
      window,
      event: {
        connectorId: trigger.connectorId,
        eventType: 'newRecord',
        at: iso(event?.at ?? firing.slotAt),
        ...(recordReadable && event?.recordId !== undefined
          ? { recordId: event.recordId }
          : {}),
      },
    };
  }
  return {
    kind: 'webhook',
    ...base,
    window,
    ...(event?.webhook !== undefined
      ? {
          webhook: {
            receivedAt: iso(event.at),
            bodyBytes: event.webhook.bodyBytes,
            bodySha256: event.webhook.bodySha256,
            ...(event.webhook.contentType !== undefined
              ? { contentType: event.webhook.contentType }
              : {}),
          },
        }
      : {}),
  };
}

export const scheduleOverlap = (
  schedule: Pick<AgentSchedule, 'overlap'>,
): OverlapPolicy => schedule.overlap ?? DEFAULT_OVERLAP;

export const scheduleNotify = (
  schedule: Pick<AgentSchedule, 'notify'>,
): NotifyChannel[] => [...(schedule.notify ?? DEFAULT_NOTIFY)];

/** Absent on a webhook made before signatures: the token in its header. */
export const webhookAuth = (
  trigger: Extract<ScheduleTrigger, { kind: 'webhook' }>,
): WebhookAuth => trigger.auth ?? 'token';

/**
 * The signature a webhook caller sends (TR-04): `v1=` and the hex HMAC-SHA256,
 * keyed by the schedule's secret, of `<timestamp>.<raw body>`. The timestamp
 * is signed so a captured request cannot be replayed after the tolerance.
 */
export function webhookSignature(
  key: string,
  timestamp: string,
  body: Buffer,
): string {
  return `v1=${createHmac('sha256', key)
    .update(`${timestamp}.`, 'utf8')
    .update(body)
    .digest('hex')}`;
}

/** Whether `given` (one or more comma-separated `v1=` values) holds the expected signature. */
export function signatureMatches(expected: string, given: string): boolean {
  const want = Buffer.from(expected, 'utf8');
  return given
    .split(',')
    .map((part) => Buffer.from(part.trim(), 'utf8'))
    .some((part) => part.length === want.length && timingSafeEqual(part, want));
}

/** The schedule and slot a scheduled run's idempotency key names; null for any other run. */
export function parseScheduleIdempotencyKey(
  key: string,
): { scheduleId: string; slotKey: string } | null {
  const match = /^schedule:([^:]+):(.+)$/.exec(key);
  return match === null
    ? null
    : { scheduleId: match[1] as string, slotKey: match[2] as string };
}

/**
 * Why a workflow did not run, as a person is told (UIADOPT2-FU-2): a closed set over the open codes a firing keeps in
 * its history (the firer's own, and the admission refusal's, by its error group). The Inbox subject carries this; a
 * client words each one. Anything else, a notice written before, is `unavailable`.
 */
export const WORKFLOW_REFUSALS = [
  'workflow_changed',
  'scheduled_runs_off',
  'no_agent_access',
  'model_not_allowed',
  'engine_not_allowed',
  'stopped_by_admin',
  'budget_reached',
  'too_busy',
  'not_found',
  'unavailable',
] as const;
export type WorkflowRefusal = (typeof WORKFLOW_REFUSALS)[number];

const FIRER_REFUSALS: Readonly<Record<string, WorkflowRefusal>> = {
  schedule_deleted: 'workflow_changed',
  schedule_disabled: 'workflow_changed',
  schedule_paused: 'workflow_changed',
  actor_changed: 'workflow_changed',
  template_missing: 'workflow_changed',
  scheduled_runs_disabled: 'scheduled_runs_off',
  rollout_legacy: 'scheduled_runs_off',
  agent_forbidden: 'no_agent_access',
  quota: 'too_busy',
  run_active: 'too_busy',
  not_found: 'not_found',
};

const GROUP_REFUSALS: Readonly<Record<string, WorkflowRefusal>> = {
  model_access: 'model_not_allowed',
  model_account: 'model_not_allowed',
  engine_profile: 'engine_not_allowed',
  group_access: 'no_agent_access',
  turn: 'no_agent_access',
  operator_control: 'stopped_by_admin',
  budget: 'budget_reached',
};

export function workflowRefusalOf(code: string): WorkflowRefusal {
  const own = FIRER_REFUSALS[code];
  if (own !== undefined) return own;
  const entry = (ERROR_CODES as Readonly<Record<string, { group: string }>>)[
    code.toUpperCase()
  ];
  return (
    (entry !== undefined ? GROUP_REFUSALS[entry.group] : undefined) ??
    'unavailable'
  );
}

const TRIGGER_INITIATOR = 'trigger:';

/**
 * The schedule that fired a run, or null for a run no schedule fired. A run stores its idempotency key as a digest
 * (QA15-F3), so the key cannot name its slot; its attribution names the schedule (`trigger:<scheduleId>`). A run an
 * older build wrote still holds the key itself.
 */
export function firedScheduleOf(run: {
  idempotencyKey: string;
  attribution?: { initiatorRef: string };
}): string | null {
  const initiator = run.attribution?.initiatorRef;
  if (initiator?.startsWith(TRIGGER_INITIATOR) === true) {
    const id = initiator.slice(TRIGGER_INITIATOR.length);
    if (id.length > 0) return id;
  }
  return parseScheduleIdempotencyKey(run.idempotencyKey)?.scheduleId ?? null;
}

export function assertScheduleBudget(maxCostUsd: number | undefined): void {
  if (maxCostUsd === undefined) return;
  if (!(maxCostUsd > 0) || maxCostUsd > RUN_SPEC_BUDGETS.maxCostUsd) {
    throw new HarnessScheduleRefusedError(
      'SCHEDULE_BUDGET_EXCEEDS_CEILING',
      `A firing's budget must be above 0 and at most ${String(RUN_SPEC_BUDGETS.maxCostUsd)} USD`,
      'maxCostUsd',
    );
  }
}

/**
 * What a refused firing does to its schedule. `retry`: transient, the firing
 * stays pending; `refuse`: this slot is lost, the schedule goes on; `pause`:
 * it will not heal by itself, the actor must look; `disable`: the actor is
 * gone, nothing may run as them again.
 */
export type FiringDisposition = 'retry' | 'refuse' | 'pause' | 'disable';

const FIRING_DISPOSITIONS: Readonly<Record<string, FiringDisposition>> = {
  unavailable: 'retry',
  actor_deleted: 'disable',
  actor_disabled: 'pause',
  actor_not_person: 'pause',
  org_inactive: 'pause',
  agent_forbidden: 'pause',
  forbidden: 'pause',
  narrowing: 'pause',
  scheduled_runs_disabled: 'refuse',
  rollout_legacy: 'refuse',
  template_missing: 'disable',
};

/** Any other reason (quota, a model-access refusal, a store conflict) loses only its slot. */
export const firingDisposition = (reason: string): FiringDisposition =>
  FIRING_DISPOSITIONS[reason] ?? 'refuse';

/** Doubling from the lease, capped; a firing is retried at most `FIRING_MAX_ATTEMPTS` times. */
export const firingBackoffMs = (attempts: number): number =>
  Math.min(
    FIRING_LEASE_MS * 2 ** Math.max(0, attempts - 1),
    FIRING_BACKOFF_MAX_MS,
  );

/** The run's own record of why it exists (store-only; pinned as `runKind: scheduled`). */
export interface RunTrigger {
  scheduleId: string;
  firingId: string;
  slotKey: string;
  maxCostUsd?: number;
}
