import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import { randomUUID } from 'node:crypto';
import type { HarnessRun, RunStatus } from '../domain/execution';
import {
  type AgentSchedule,
  coalescedSlot,
  cronSlotKey,
  decideCron,
  eventSlotKey,
  FIRING_LEASE_MS,
  FIRING_MAX_ATTEMPTS,
  firingAgentVersion,
  firingBackoffMs,
  firingDisposition,
  type FiringEvent,
  firingInput,
  type FiringKind,
  manualSlotKey,
  MAX_DUE_PER_TICK,
  MAX_WATCHED_PER_TICK,
  type NotifyChannel,
  OVERLAP_RECHECK_MS,
  firedScheduleOf,
  parseScheduleIdempotencyKey,
  type RunTriggerContext,
  SCHEDULE_TICK_MS,
  type ScheduleFiring,
  scheduleIdempotencyKey,
  scheduleNotify,
  scheduleOverlap,
  triggerContext,
  type TriggerEvent,
} from '../domain/schedules';
import type {
  ActorAuthority,
  RunDigestReader,
  RunStatusReader,
  ScheduledRunAdmission,
  ScheduledRunsSetting,
  ScheduledWaitNotices,
  ScheduleNotifier,
  ScheduleStore,
  TriggerSettings,
} from '../ports/schedules';
import type { RecordReadAccess } from '../ports/record-access';
import { sourceKey } from '../projection/citation-source';
import type { RunDigest } from '../projection/run-digest';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { detachedInterval } from '../../../libs/context/orgContext';

/** A firer's per-schedule work is bound to the org of the schedule or firing its cross-org read found. */
const SCHEDULE_JOB = 'agent-harness.schedule-firer';

const ENDED: ReadonlySet<RunStatus> = new Set([
  'completed',
  'failed',
  'cancelled',
]);
/** The schedule's latest admitted firings an overlap check looks at. */
const MAX_ACTIVE_CHECKED = 10;

/** What the firer reads of a run the execution service tells it of. */
type FiredRun = Pick<
  HarnessRun,
  'orgId' | 'runId' | 'idempotencyKey' | 'status'
> &
  Partial<Pick<HarnessRun, 'attribution'>>;

/** Why an attempt admitted nothing yet leaves the firing as it should be: queued behind a run, or skipped. */
type Waiting = { waiting: 'queued' } | { skipped: string };

export interface ScheduleFirerOptions {
  store: ScheduleStore;
  admission: ScheduledRunAdmission;
  actors: ActorAuthority;
  setting: ScheduledRunsSetting;
  runs?: RunStatusReader;
  notifier?: ScheduleNotifier;
  /** AP-10: what the actor is sent besides the inbox notice when a run waits on them. Absent: the inbox notice only. */
  waitNotices?: ScheduledWaitNotices;
  /** A run's end-of-run summary for its notice (G-07). Absent: notices carry none. */
  digests?: RunDigestReader;
  /** Connector events from the broker: consumed only while a connector trigger is active. */
  connectorEvents?: TriggerSourceSwitch;
  /**
   * Whether the actor may read the record a connector event announced (TR-03),
   * asked with their own credential. Absent: no record is named to the run.
   */
  records?: RecordReadAccess;
  /** The org's allowed notice channels (TR-07). Absent: every channel the schedule names. */
  settings?: Pick<TriggerSettings, 'notifyChannels'>;
  logger?: HarnessLogger;
  newId?: () => string;
}

/** What a firing keeps of its event: references, sizes and the time. */
const firingEvent = (event: TriggerEvent): FiringEvent => ({
  ...(event.connectorId !== undefined
    ? { connectorId: event.connectorId }
    : {}),
  ...(event.recordId !== undefined ? { recordId: event.recordId } : {}),
  ...(event.webhook !== undefined ? { webhook: { ...event.webhook } } : {}),
  at: event.at,
});

export interface TriggerSourceSwitch {
  ensure(wanted: boolean): Promise<void>;
  stop(): Promise<void>;
}

const errorType = (error: unknown): string =>
  error instanceof Error ? error.name : typeof error;

/**
 * Fires schedules (P8a). Every replica runs one; none leads. A slot fires once
 * because claiming it inserts its firing under a unique key and moves the
 * schedule's cursor in one store write, and its run is admitted with an
 * idempotency key derived from the slot, so a firing retried after a crash
 * replays the run admission already made. The actor is checked again at each
 * firing; the run never acts as anyone else.
 */
/** A schedule's actor is told of a refused firing at most this often (UX3). */
export const REFUSAL_NOTICE_INTERVAL_MS = 24 * 60 * 60 * 1000;
/** How many of a schedule's latest firings say whether it was told today. */
const REFUSAL_NOTICE_LOOKBACK = 50;

export class ScheduleFirer {
  private readonly store: ScheduleStore;
  private readonly admission: ScheduledRunAdmission;
  private readonly actors: ActorAuthority;
  private readonly setting: ScheduledRunsSetting;
  private readonly runs?: RunStatusReader;
  private readonly notifier?: ScheduleNotifier;
  private readonly waitNotices?: ScheduledWaitNotices;
  private readonly digests?: RunDigestReader;
  private readonly connectorEvents?: TriggerSourceSwitch;
  private readonly records?: RecordReadAccess;
  private readonly settings?: Pick<TriggerSettings, 'notifyChannels'>;
  private readonly logger: HarnessLogger;
  private readonly newId: () => string;
  private timer: NodeJS.Timeout | null = null;
  private ticking: Promise<void> | null = null;

  constructor(options: ScheduleFirerOptions) {
    this.store = options.store;
    this.admission = options.admission;
    this.actors = options.actors;
    this.setting = options.setting;
    this.runs = options.runs;
    this.notifier = options.notifier;
    this.waitNotices = options.waitNotices;
    this.digests = options.digests;
    this.connectorEvents = options.connectorEvents;
    this.records = options.records;
    this.settings = options.settings;
    this.logger = options.logger ?? silentHarnessLogger;
    this.newId = options.newId ?? randomUUID;
  }

  start(intervalMs = SCHEDULE_TICK_MS): void {
    if (this.timer !== null) return;
    this.timer = detachedInterval(() => {
      if (this.ticking !== null) return;
      this.ticking = this.tick().finally(() => {
        this.ticking = null;
      });
    }, intervalMs);
  }

  async stop(): Promise<void> {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    await this.ticking;
    await this.connectorEvents?.stop();
  }

  /** One pass: due cron slots, pending firings whose lease passed, fired runs waiting for approval or ended. */
  async tick(): Promise<void> {
    for (const [phase, step] of [
      ['due', () => this.fireDue()],
      ['stale', () => this.retryStale()],
      ['watch', () => this.watchRuns()],
      ['sources', () => this.switchSources()],
    ] as const) {
      try {
        await step();
      } catch (error) {
        this.logger.warn(
          'schedule.tick_failed',
          harnessLogMeta('schedule.tick_failed', {
            phase,
            errorType: errorType(error),
          }),
        );
      }
    }
  }

  private async switchSources(): Promise<void> {
    if (this.connectorEvents === undefined) return;
    await this.connectorEvents.ensure(await this.store.hasActive('connector'));
  }

  async fireDue(): Promise<number> {
    const now = await this.store.now();
    let fired = 0;
    for (const schedule of await this.store.dueCron(now, MAX_DUE_PER_TICK)) {
      if (
        await forEnumeratedOrg(schedule.orgId, SCHEDULE_JOB, () =>
          this.fireSlot(schedule, now),
        )
      )
        fired += 1;
    }
    return fired;
  }

  /** One due schedule's slot, as the schedule's org: claimed and fired, or skipped; true when it fired. */
  private async fireSlot(
    schedule: AgentSchedule,
    now: number,
  ): Promise<boolean> {
    if (schedule.trigger.kind !== 'cron' || schedule.nextFireAt === null) {
      return false;
    }
    const decision = decideCron(schedule.trigger, schedule.nextFireAt, now);
    if (decision.kind === 'wait') return false;
    const firing = this.newFiring(schedule, {
      kind: 'cron',
      slotKey: cronSlotKey(decision.slotAt),
      slotAt: decision.slotAt,
      now,
      missed: decision.missed,
      skipped: decision.kind === 'skip',
    });
    const claimed = await this.store.claimCronSlot(
      firing,
      schedule.nextFireAt,
      decision.nextFireAt,
    );
    if (claimed === null) return false;
    if (decision.kind === 'skip') {
      this.logger.info(
        'schedule.slot_skipped',
        harnessLogMeta('schedule.slot_skipped', {
          ...this.fields(claimed),
          missed: decision.missed + 1,
          reason: 'catch_up_skip',
        }),
      );
      return false;
    }
    await this.fire(claimed, schedule);
    return true;
  }

  /**
   * A run now (SV-5): the caller's key names the slot, so the same key
   * answers the same firing and its run.
   */
  async runNow(schedule: AgentSchedule, key: string): Promise<ScheduleFiring> {
    const now = await this.store.now();
    const { firing, created } = await this.store.claimSlot(
      this.newFiring(schedule, {
        kind: 'manual',
        slotKey: manualSlotKey(key),
        slotAt: now,
        now,
        missed: 0,
        skipped: false,
      }),
    );
    if (created) await this.fire(firing, schedule);
    return (await this.store.getFiring(firing.orgId, firing.id)) ?? firing;
  }

  /** A webhook or connector event: one firing per schedule and coalescing window. */
  async deliver(
    event: TriggerEvent,
    only?: AgentSchedule,
  ): Promise<ScheduleFiring[]> {
    const schedules =
      only !== undefined
        ? [only]
        : await this.store.eventSchedules(
            event.orgId,
            event.kind,
            event.connectorId,
          );
    const fired: ScheduleFiring[] = [];
    for (const schedule of schedules) {
      const trigger = schedule.trigger;
      if (
        schedule.status !== 'active' ||
        trigger.kind === 'cron' ||
        trigger.kind !== event.kind
      ) {
        continue;
      }
      const slotAt = coalescedSlot(event.at, trigger.coalesceS);
      const { firing, created } = await this.store.claimSlot({
        ...this.newFiring(schedule, {
          kind: 'event',
          slotKey: eventSlotKey(slotAt),
          slotAt,
          now: await this.store.now(),
          missed: 0,
          skipped: false,
        }),
        event: firingEvent(event),
      });
      if (!created) continue;
      await this.fire(firing, schedule);
      fired.push(
        (await this.store.getFiring(firing.orgId, firing.id)) ?? firing,
      );
    }
    return fired;
  }

  async retryStale(): Promise<number> {
    const now = await this.store.now();
    const stale = await this.store.takeStale(
      now,
      FIRING_LEASE_MS,
      MAX_DUE_PER_TICK,
    );
    for (const firing of stale)
      await forEnumeratedOrg(firing.orgId, SCHEDULE_JOB, () =>
        this.fire(firing),
      );
    return stale.length;
  }

  /**
   * The fired runs still watched, oldest-checked first. Approvals at 3 a.m.
   * (P2.6c): a run that suspended for an approval waits until its deadline;
   * its actor is told once, and the run sweeper expires it at the deadline if
   * nobody decides. A run that ended is told to its actor once (TR-07): the
   * run's own end usually tells it first (`runEnded`); this is the backstop
   * for a replica that died before it could.
   */
  async watchRuns(): Promise<number> {
    if (this.runs === undefined) return 0;
    const now = await this.store.now();
    let notified = 0;
    for (const firing of await this.store.watched(now, MAX_WATCHED_PER_TICK)) {
      if (
        await forEnumeratedOrg(firing.orgId, SCHEDULE_JOB, () =>
          this.watchOne(firing, now),
        )
      )
        notified += 1;
    }
    return notified;
  }

  /** One watched firing, as its org; true when its actor was told. */
  private async watchOne(
    firing: ScheduleFiring,
    now: number,
  ): Promise<boolean> {
    const runs = this.runs;
    if (runs === undefined) return false;
    await this.store.markChecked(firing.orgId, firing.id, now);
    if (firing.runId === null) return false;
    const status = await runs.status(firing.orgId, firing.runId);
    if (status === null || ENDED.has(status)) {
      return this.endWatch(firing, status, now);
    }
    return this.observe(firing, firing.runId, status, now);
  }

  /**
   * A scheduled run moved short of an end (UIB5-FU-1: the execution service tells this replica's firer of each run a
   * command moved): it is told now, not at the next watch, which stays the backstop. Never throws.
   */
  async runMoved(run: FiredRun): Promise<void> {
    if (ENDED.has(run.status)) return;
    const scheduleId = firedScheduleOf(run);
    if (scheduleId === null) return;
    try {
      const firing = await this.firingOfRun(run, scheduleId);
      if (firing === null || firing.watchUntil === null) return;
      await this.observe(firing, run.runId, run.status, await this.store.now());
    } catch (error) {
      this.logger.warn(
        'schedule.run_move_failed',
        harnessLogMeta('schedule.run_move_failed', {
          orgId: run.orgId,
          runId: run.runId,
          scheduleId,
          errorType: errorType(error),
        }),
      );
    }
  }

  /**
   * The firing whose run this is. A run stores its idempotency key as a digest (QA15-F3), so the slot cannot be read
   * back from it: the firing is the schedule's watched one holding this run. A run an older build wrote still holds
   * the key itself, which names its slot.
   */
  private async firingOfRun(
    run: FiredRun,
    scheduleId: string,
  ): Promise<ScheduleFiring | null> {
    const key = parseScheduleIdempotencyKey(run.idempotencyKey);
    const firing =
      key !== null
        ? await this.store.firingBySlot(run.orgId, key.scheduleId, key.slotKey)
        : ((
            await this.store.activeFirings(
              run.orgId,
              scheduleId,
              MAX_ACTIVE_CHECKED,
            )
          ).find((f) => f.runId === run.runId) ?? null);
    return firing?.runId === run.runId ? firing : null;
  }

  /** A watched firing's run, still going, at `status`: a first wait is told to its actor once. */
  private async observe(
    firing: ScheduleFiring,
    runId: string,
    status: RunStatus,
    now: number,
  ): Promise<boolean> {
    let notifiedNow = false;
    if (firing.watchUntil === null || firing.watchUntil <= now) return false;
    if (status !== 'waiting_approval' || firing.notifiedAt !== null)
      return false;
    if (!(await this.store.markNotified(firing.orgId, firing.id, now))) {
      return false;
    }
    const schedule = await this.store.get(firing.orgId, firing.scheduleId);
    try {
      await this.notifier?.approvalWaiting({
        orgId: firing.orgId,
        actorId: firing.actorId,
        scheduleId: firing.scheduleId,
        scheduleName: schedule?.name ?? '',
        ...(schedule?.agentKey !== undefined
          ? { agentKey: schedule.agentKey }
          : {}),
        sessionId: firing.sessionId ?? '',
        runId: runId,
        deadlineAt: firing.watchUntil,
      });
      if (schedule !== null && this.waitNotices !== undefined) {
        const channels = (await this.channels(schedule)).filter(
          (c): c is 'email' | 'slack' => c === 'email' || c === 'slack',
        );
        if (channels.length > 0) {
          await this.waitNotices.runWaiting({
            orgId: firing.orgId,
            actorId: firing.actorId,
            scheduleName: schedule.name,
            ...(schedule.agentKey !== undefined
              ? { agentKey: schedule.agentKey }
              : {}),
            runId,
            channels,
          });
        }
      }
      notifiedNow = true;
      this.logger.info(
        'schedule.approval_notified',
        harnessLogMeta('schedule.approval_notified', {
          ...this.fields(firing),
          runId: runId,
          sessionId: firing.sessionId ?? undefined,
          deadlineAt: firing.watchUntil,
        }),
      );
    } catch (error) {
      this.logger.warn(
        'schedule.notify_failed',
        harnessLogMeta('schedule.notify_failed', {
          ...this.fields(firing),
          runId: runId,
          errorType: errorType(error),
        }),
      );
    }
    return notifiedNow;
  }

  /**
   * A run ended (the execution service tells this replica's firer of each run
   * it ended). A scheduled run's actor is told now, and a firing queued behind
   * it (TR-06) is made due for the next tick. Never throws.
   */
  async runEnded(run: FiredRun): Promise<void> {
    const scheduleId = firedScheduleOf(run);
    if (scheduleId === null) return;
    try {
      const now = await this.store.now();
      const firing = await this.firingOfRun(run, scheduleId);
      if (firing !== null && ENDED.has(run.status)) {
        await this.endWatch(firing, run.status, now);
      }
      const queued = await this.store.queuedFiring(run.orgId, scheduleId);
      if (queued?.queuedBehind === run.runId) {
        await this.store.release(queued.orgId, queued.id, queued.attempts, now);
      }
    } catch (error) {
      this.logger.warn(
        'schedule.run_end_failed',
        harnessLogMeta('schedule.run_end_failed', {
          orgId: run.orgId,
          runId: run.runId,
          scheduleId,
          errorType: errorType(error),
        }),
      );
    }
  }

  /** The run's summary (G-07); null when it cannot be read: the notice still goes, without it. */
  private async digestOf(
    firing: ScheduleFiring,
    runId: string,
    status: 'completed' | 'failed' | 'cancelled',
  ): Promise<RunDigest | null> {
    if (this.digests === undefined) return null;
    try {
      return await this.digests.digest(firing.orgId, runId, status);
    } catch (error) {
      this.logger.warn(
        'schedule.run_digest_failed',
        harnessLogMeta('schedule.run_digest_failed', {
          ...this.fields(firing),
          runId,
          errorType: errorType(error),
        }),
      );
      return null;
    }
  }

  /**
   * Ends a firing's watch and, for the caller that ended it, tells the actor
   * the run ended on the channels the schedule and the org allow. True when a
   * notice went out.
   */
  private async endWatch(
    firing: ScheduleFiring,
    status: RunStatus | null,
    now: number,
  ): Promise<boolean> {
    if (!(await this.store.unwatch(firing.orgId, firing.id, now))) return false;
    if (
      firing.runId === null ||
      (status !== 'completed' && status !== 'failed' && status !== 'cancelled')
    ) {
      return false;
    }
    const schedule = await this.store.get(firing.orgId, firing.scheduleId);
    if (schedule === null || this.notifier === undefined) return false;
    try {
      const channels = await this.channels(schedule);
      if (channels.length === 0) return false;
      const digest = await this.digestOf(firing, firing.runId, status);
      await this.notifier.runEnded({
        orgId: firing.orgId,
        actorId: firing.actorId,
        scheduleId: firing.scheduleId,
        scheduleName: schedule.name,
        ...(schedule.agentKey !== undefined
          ? { agentKey: schedule.agentKey }
          : {}),
        sessionId: firing.sessionId ?? '',
        runId: firing.runId,
        status,
        channels,
        ...(digest !== null ? { digest } : {}),
      });
      this.logger.info(
        'schedule.run_end_notified',
        harnessLogMeta('schedule.run_end_notified', {
          ...this.fields(firing),
          runId: firing.runId,
          status,
          channels: channels.join(','),
        }),
      );
      return true;
    } catch (error) {
      this.logger.warn(
        'schedule.notify_failed',
        harnessLogMeta('schedule.notify_failed', {
          ...this.fields(firing),
          runId: firing.runId,
          errorType: errorType(error),
        }),
      );
      return false;
    }
  }

  /** The schedule's channels the org allows; an unreadable setting allows in-app only. */
  private async channels(schedule: AgentSchedule): Promise<NotifyChannel[]> {
    const wanted = scheduleNotify(schedule);
    if (this.settings === undefined) return wanted;
    let allowed: readonly NotifyChannel[];
    try {
      allowed = await this.settings.notifyChannels(schedule.orgId);
    } catch {
      allowed = ['in_app'];
    }
    return wanted.filter((channel) => allowed.includes(channel));
  }

  private newFiring(
    schedule: AgentSchedule,
    slot: {
      kind: FiringKind;
      slotKey: string;
      slotAt: number;
      now: number;
      missed: number;
      skipped: boolean;
    },
  ): ScheduleFiring {
    return {
      id: this.newId(),
      orgId: schedule.orgId,
      scheduleId: schedule.id,
      actorId: schedule.actorId,
      slotKey: slot.slotKey,
      slotAt: slot.slotAt,
      kind: slot.kind,
      status: slot.skipped ? 'skipped' : 'pending',
      runId: null,
      sessionId: null,
      reason: slot.skipped ? 'catch_up_skip' : null,
      missed: slot.missed,
      attempts: slot.skipped ? 0 : 1,
      leaseUntil: slot.now + FIRING_LEASE_MS,
      createdAt: slot.now,
      settledAt: slot.skipped ? slot.now : null,
      watchUntil: null,
      notifiedAt: null,
    };
  }

  private fields(firing: ScheduleFiring): Record<string, string | number> {
    return {
      orgId: firing.orgId,
      scheduleId: firing.scheduleId,
      actorId: firing.actorId,
      firingId: firing.id,
      slotKey: firing.slotKey,
      kind: firing.kind,
      attempt: firing.attempts,
    };
  }

  /** Admits the firing's run as its actor, or settles why not. Never throws. */
  private async fire(
    firing: ScheduleFiring,
    known?: AgentSchedule,
  ): Promise<void> {
    let result: string | Waiting;
    try {
      result = await this.attempt(firing, known);
    } catch (error) {
      this.logger.warn(
        'schedule.firing_failed',
        harnessLogMeta('schedule.firing_failed', {
          ...this.fields(firing),
          errorType: errorType(error),
        }),
      );
      result = 'unavailable';
    }
    if (result !== 'admitted') await this.settleUnadmitted(firing, result);
  }

  private async settleUnadmitted(
    firing: ScheduleFiring,
    result: string | Waiting,
  ): Promise<void> {
    try {
      if (typeof result === 'string') {
        await this.refuse(firing, result);
      } else if ('skipped' in result) {
        await this.skip(firing, result.skipped);
      }
    } catch (error) {
      // The firing stays pending; its lease passes and another tick takes it.
      this.logger.warn(
        'schedule.firing_failed',
        harnessLogMeta('schedule.firing_failed', {
          ...this.fields(firing),
          reason: typeof result === 'string' ? result : undefined,
          errorType: errorType(error),
        }),
      );
    }
  }

  /**
   * 'admitted' when the run was admitted and the firing settled; a waiting
   * outcome when an earlier run is still active (TR-06); otherwise the
   * refusal's reason.
   */
  private async attempt(
    firing: ScheduleFiring,
    known?: AgentSchedule,
  ): Promise<string | Waiting> {
    const schedule =
      known ?? (await this.store.get(firing.orgId, firing.scheduleId));
    if (schedule === null) return 'schedule_deleted';
    if (schedule.actorId !== firing.actorId) return 'actor_changed';
    if (schedule.status === 'disabled') return 'schedule_disabled';
    if (schedule.status === 'paused' && firing.kind !== 'manual') {
      return 'schedule_paused';
    }
    if (!(await this.enabled(schedule.orgId))) return 'scheduled_runs_disabled';
    if (schedule.input === null) return 'template_missing';
    // A person's run now is theirs to start; the policy is for what fires by itself.
    if (firing.kind !== 'manual') {
      const waiting = await this.overlap(schedule, firing);
      if (waiting !== null) return waiting;
    }
    const actor = await this.actors.authorize(schedule.orgId, schedule.actorId);
    if ('refused' in actor) return actor.refused;
    const context = await this.context(schedule, firing, actor.authorization);
    const input = firingInput(schedule, firing.slotAt, context);
    if (input === null) return 'template_missing';
    const agentVersion = firingAgentVersion(schedule);
    const outcome = await this.admission.admit({
      orgId: schedule.orgId,
      actorId: schedule.actorId,
      ...(schedule.agentKey !== undefined
        ? { agentKey: schedule.agentKey }
        : {}),
      ...(agentVersion !== undefined ? { agentVersion } : {}),
      idempotencyKey: scheduleIdempotencyKey(schedule.id, firing.slotKey),
      deadlineMs: schedule.deadlineS * 1000,
      input,
      authorization: actor.authorization,
      trigger: {
        scheduleId: schedule.id,
        firingId: firing.id,
        slotKey: firing.slotKey,
        ...(schedule.maxCostUsd !== undefined
          ? { maxCostUsd: schedule.maxCostUsd }
          : {}),
      },
    });
    if (outcome.kind === 'refused') return outcome.reason;
    const now = await this.store.now();
    await this.store.settle(
      firing.orgId,
      firing.id,
      {
        status: 'admitted',
        runId: outcome.runId,
        sessionId: outcome.sessionId,
        watchUntil: now + schedule.deadlineS * 1000,
      },
      now,
    );
    this.logger.info(
      'schedule.fired',
      harnessLogMeta('schedule.fired', {
        ...this.fields(firing),
        runId: outcome.runId,
        sessionId: outcome.sessionId,
        agentKey: schedule.agentKey,
        lateMs: now - firing.slotAt,
        missed: firing.missed,
      }),
    );
    return 'admitted';
  }

  /**
   * TR-06: what the schedule's overlap policy does while an earlier run of it
   * is active; null when none is, or the policy allows overlap. A queued
   * firing waits behind that run and looks again when it ends (`runEnded`) or
   * after `OVERLAP_RECHECK_MS`; one queued firing at most, a later one skips.
   */
  private async overlap(
    schedule: AgentSchedule,
    firing: ScheduleFiring,
  ): Promise<Waiting | null> {
    const policy = scheduleOverlap(schedule);
    if (policy === 'allow' || this.runs === undefined) return null;
    const active = await this.activeRun(schedule, firing);
    if (active === null) return null;
    if (policy === 'skip') return { skipped: 'overlap_skip' };
    const queued = await this.store.queuedFiring(schedule.orgId, schedule.id);
    if (queued !== null && queued.id !== firing.id) {
      return { skipped: 'overlap_queue_full' };
    }
    const now = await this.store.now();
    if (
      await this.store.requeue(
        firing.orgId,
        firing.id,
        firing.attempts,
        active,
        now + OVERLAP_RECHECK_MS,
      )
    ) {
      this.logger.info(
        'schedule.firing_queued',
        harnessLogMeta('schedule.firing_queued', {
          ...this.fields(firing),
          behindRunId: active,
        }),
      );
    }
    return { waiting: 'queued' };
  }

  /** An earlier firing's run of the schedule that has not ended, if any. */
  private async activeRun(
    schedule: AgentSchedule,
    firing: ScheduleFiring,
  ): Promise<string | null> {
    const runs = this.runs;
    if (runs === undefined) return null;
    for (const other of await this.store.activeFirings(
      schedule.orgId,
      schedule.id,
      MAX_ACTIVE_CHECKED,
    )) {
      if (other.id === firing.id || other.runId === null) continue;
      const status = await runs.status(other.orgId, other.runId);
      if (status !== null && !ENDED.has(status)) return other.runId;
    }
    return null;
  }

  /**
   * The firing's trigger context (TR-03), fixed at its first attempt: a
   * connector record is named only when the actor may read it now, asked
   * with their own credential; an outage rejects, and the firing retries.
   */
  private async context(
    schedule: AgentSchedule,
    firing: ScheduleFiring,
    authorization: string,
  ): Promise<RunTriggerContext> {
    if (firing.context !== undefined) return firing.context;
    const recordId = firing.event?.recordId;
    const readable =
      schedule.trigger.kind === 'connector' &&
      recordId !== undefined &&
      (await this.recordReadable(authorization, recordId));
    return this.store.fixContext(
      firing.orgId,
      firing.id,
      triggerContext(schedule, firing, readable),
    );
  }

  private async recordReadable(
    authorization: string,
    recordId: string,
  ): Promise<boolean> {
    if (this.records === undefined) return false;
    const source = { kind: 'indexed', recordId } as const;
    const decided = await this.records.decide({
      authorization,
      sources: [source],
    });
    return decided.get(sourceKey(source)) === 'readable';
  }

  /** A slot the overlap policy passed over: settled skipped, with why. */
  private async skip(firing: ScheduleFiring, reason: string): Promise<void> {
    const now = await this.store.now();
    if (
      !(await this.store.settle(
        firing.orgId,
        firing.id,
        { status: 'skipped', reason },
        now,
      ))
    ) {
      return;
    }
    this.logger.info(
      'schedule.slot_skipped',
      harnessLogMeta('schedule.slot_skipped', {
        ...this.fields(firing),
        reason,
      }),
    );
  }

  private async refuse(firing: ScheduleFiring, reason: string): Promise<void> {
    const disposition = firingDisposition(reason);
    const now = await this.store.now();
    if (disposition === 'retry' && firing.attempts < FIRING_MAX_ATTEMPTS) {
      await this.store.release(
        firing.orgId,
        firing.id,
        firing.attempts,
        now + firingBackoffMs(firing.attempts),
      );
      this.logger.warn(
        'schedule.firing_retry',
        harnessLogMeta('schedule.firing_retry', {
          ...this.fields(firing),
          reason,
        }),
      );
      return;
    }
    await this.store.settle(
      firing.orgId,
      firing.id,
      { status: 'refused', reason },
      now,
    );
    this.logger.warn(
      'schedule.firing_refused',
      harnessLogMeta('schedule.firing_refused', {
        ...this.fields(firing),
        reason,
        disposition,
      }),
    );
    await this.tellRefused(firing, reason, now);
    if (disposition !== 'pause' && disposition !== 'disable') return;
    const changed = await this.store.setStatus(
      firing.orgId,
      firing.scheduleId,
      disposition === 'disable'
        ? {
            from: ['active', 'paused'],
            to: 'disabled',
            reason,
            nextFireAt: null,
          }
        : { from: ['active'], to: 'paused', reason, nextFireAt: null },
      now,
    );
    if (changed === null) return;
    this.logger.warn(
      'schedule.auto_paused',
      harnessLogMeta('schedule.auto_paused', {
        orgId: firing.orgId,
        scheduleId: firing.scheduleId,
        actorId: firing.actorId,
        firingId: firing.id,
        status: changed.status,
        reason,
      }),
    );
  }

  /**
   * The actor hears that their schedule could not run (UX3): once a day per
   * schedule, read from its own firings, so every scheduler instance agrees.
   */
  private async tellRefused(
    firing: ScheduleFiring,
    reason: string,
    now: number,
  ): Promise<void> {
    if (this.notifier === undefined) return;
    try {
      const recent = await this.store.firings(
        firing.orgId,
        firing.scheduleId,
        REFUSAL_NOTICE_LOOKBACK,
      );
      const toldToday = recent.some(
        (other) =>
          other.id !== firing.id &&
          other.status === 'refused' &&
          other.settledAt !== null &&
          now - other.settledAt < REFUSAL_NOTICE_INTERVAL_MS,
      );
      if (toldToday) return;
      const schedule = await this.store.get(firing.orgId, firing.scheduleId);
      await this.notifier.firingRefused({
        orgId: firing.orgId,
        actorId: firing.actorId,
        scheduleId: firing.scheduleId,
        scheduleName: schedule?.name ?? '',
        reason,
      });
      this.logger.info(
        'schedule.refusal_notified',
        harnessLogMeta('schedule.refusal_notified', {
          ...this.fields(firing),
          reason,
        }),
      );
    } catch (error) {
      this.logger.warn(
        'schedule.notify_failed',
        harnessLogMeta('schedule.notify_failed', {
          ...this.fields(firing),
          errorType: errorType(error),
        }),
      );
    }
  }

  private async enabled(orgId: string): Promise<boolean> {
    try {
      return await this.setting.enabled(orgId);
    } catch {
      return false;
    }
  }
}
