import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import { orgSchedulesOn } from '../config';
import { randomUUID } from 'node:crypto';
import { HarnessScheduleRefusedError } from '../domain/errors';
import type { HarnessRun } from '../domain/execution';
import {
  type AgentSchedule,
  coalescedSlot,
  cronSlotKey,
  type CronDecision,
  decideCron,
  decideOnce,
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
  MAX_ONCE_LATENESS_MS,
  ONCE_TOO_LATE,
  onceSlotKey,
  RAN_ONCE,
  OVERLAP_RECHECK_MS,
  firedScheduleOf,
  firingRun,
  type ScheduleRunnerKind,
  parseScheduleIdempotencyKey,
  type RunTriggerContext,
  SCHEDULE_TICK_MS,
  scheduleRevision,
  SKIPPED_BY_OWNER,
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
import {
  type ScheduleRunner,
  type ScheduleRunRequest,
  type ScheduleRunStart,
  type ScheduleRunState,
  type ScheduleRunStatus,
} from '../ports/schedule-runner';
import {
  HarnessScheduleRunner,
  scheduleRunStatus,
} from './harness-schedule-runner';
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

const ENDED_SCHEDULE_RUN: ReadonlySet<ScheduleRunStatus> = new Set([
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
  /**
   * The durable runner (ADR-SCH-02 D2). Absent: one is made of `admission`, `runs` and `digests`; without `runs` its
   * firings are not watched.
   */
  harness?: ScheduleRunner;
  /** The classic chat turn (D3), for an org whose harness is off. Absent: such a firing is refused. */
  classic?: ScheduleRunner;
  admission?: ScheduledRunAdmission;
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
  /** The org harness predicate (ADR-OPS-08: switch and Labs), which picks each firing's runner. Absent: on. */
  harnessOn?: (orgId: string) => Promise<boolean>;
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
  private readonly runners: Partial<Record<ScheduleRunnerKind, ScheduleRunner>>;
  /** The runners whose firings are watched until their run ends. */
  private readonly watching: ReadonlySet<ScheduleRunnerKind>;
  private readonly actors: ActorAuthority;
  private readonly setting: ScheduledRunsSetting;
  private readonly notifier?: ScheduleNotifier;
  private readonly waitNotices?: ScheduledWaitNotices;
  private readonly connectorEvents?: TriggerSourceSwitch;
  private readonly records?: RecordReadAccess;
  private readonly settings?: Pick<TriggerSettings, 'notifyChannels'>;
  private readonly harnessOn?: (orgId: string) => Promise<boolean>;
  private readonly logger: HarnessLogger;
  private readonly newId: () => string;
  private timer: NodeJS.Timeout | null = null;
  private ticking: Promise<void> | null = null;

  constructor(options: ScheduleFirerOptions) {
    this.store = options.store;
    const harness = harnessRunnerOf(options);
    this.runners = {
      ...(harness !== null ? { harness } : {}),
      ...(options.classic !== undefined ? { classic: options.classic } : {}),
    };
    this.watching = new Set<ScheduleRunnerKind>([
      ...(options.harness !== undefined || options.runs !== undefined
        ? (['harness'] as const)
        : []),
      ...(options.classic !== undefined ? (['classic'] as const) : []),
    ]);
    this.actors = options.actors;
    this.setting = options.setting;
    this.notifier = options.notifier;
    this.waitNotices = options.waitNotices;
    this.connectorEvents = options.connectorEvents;
    this.records = options.records;
    this.settings = options.settings;
    this.harnessOn = options.harnessOn;
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

  /**
   * One due schedule's slot, as the schedule's org: claimed and fired, or skipped; true when it fired. A one-time
   * schedule's claim also disables it (`ran_once`), in the same store write, so no firer fires it again.
   */
  private async fireSlot(
    schedule: AgentSchedule,
    now: number,
  ): Promise<boolean> {
    const trigger = schedule.trigger;
    if (schedule.nextFireAt === null) return false;
    let decision: CronDecision;
    if (trigger.kind === 'cron') {
      decision = decideCron(trigger, schedule.nextFireAt, now);
    } else if (trigger.kind === 'once') {
      decision = decideOnce(schedule.nextFireAt, now);
    } else {
      return false;
    }
    if (decision.kind === 'wait') return false;
    const once = trigger.kind === 'once';
    // ADR-SCH-03 D4: the slot its owner asked to skip is claimed as skipped, so it is recorded and never runs.
    const byOwner =
      decision.kind === 'fire' &&
      !once &&
      schedule.skipNextAt !== undefined &&
      schedule.skipNextAt === decision.slotAt;
    if (byOwner) decision = { ...decision, kind: 'skip' };
    const skipReason = byOwner
      ? SKIPPED_BY_OWNER
      : once
        ? ONCE_TOO_LATE
        : 'catch_up_skip';
    const firing = this.newFiring(schedule, {
      kind: once ? 'once' : 'cron',
      slotKey: once
        ? onceSlotKey(decision.slotAt)
        : cronSlotKey(decision.slotAt),
      slotAt: decision.slotAt,
      now,
      missed: decision.missed,
      skipped: decision.kind === 'skip' ? skipReason : null,
    });
    // Checked against the revision read: a person's edit, skip or pause since makes it miss, and the next read
    // decides on the schedule as it is now (a one-off made a cron, a skip set or cleared).
    const claimed = await this.store.claimCronSlot(
      firing,
      schedule.nextFireAt,
      decision.nextFireAt,
      once ? RAN_ONCE : undefined,
      scheduleRevision(schedule),
    );
    if (claimed === null) return false;
    if (decision.kind === 'skip') {
      this.logger.info(
        'schedule.slot_skipped',
        harnessLogMeta('schedule.slot_skipped', {
          ...this.fields(claimed),
          missed: decision.missed + 1,
          reason: skipReason,
        }),
      );
      // Nothing will ever run of it: its person hears so, as of a refused firing.
      if (once) await this.tellRefused(claimed, ONCE_TOO_LATE, now);
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
    if (schedule.trigger.kind === 'once') return this.runOnceNow(schedule, now);
    const { firing, created } = await this.store.claimSlot(
      this.newFiring(schedule, {
        kind: 'manual',
        slotKey: manualSlotKey(key),
        slotAt: now,
        now,
        missed: 0,
        skipped: null,
      }),
    );
    if (created) await this.fire(firing, schedule);
    return (await this.store.getFiring(firing.orgId, firing.id)) ?? firing;
  }

  /**
   * A one-time schedule's run now takes its one slot, as a tick would: by the
   * same compare-and-set of the cursor, so a tick and a run now racing make
   * one firing; whoever loses is told it already ran.
   */
  private async runOnceNow(
    schedule: AgentSchedule,
    now: number,
  ): Promise<ScheduleFiring> {
    if (schedule.trigger.kind !== 'once') throw new Error('not one-time');
    const firing = this.newFiring(schedule, {
      kind: 'once',
      slotKey: onceSlotKey(Date.parse(schedule.trigger.at)),
      slotAt: now,
      now,
      missed: 0,
      skipped: null,
    });
    let claimed: ScheduleFiring | null = null;
    let known = schedule;
    if (schedule.status === 'active' && schedule.nextFireAt !== null) {
      claimed = await this.store.claimCronSlot(
        firing,
        schedule.nextFireAt,
        null,
        RAN_ONCE,
        scheduleRevision(schedule),
      );
    } else if (schedule.status === 'paused') {
      // Paused, it has no cursor to move: its slot key is what keeps it to one firing, and it is disabled after.
      const slot = await this.store.claimSlot(firing);
      claimed = slot.created ? slot.firing : null;
      const disabled =
        claimed === null
          ? null
          : await this.store.setStatus(
              schedule.orgId,
              schedule.id,
              {
                from: ['paused'],
                to: 'disabled',
                reason: RAN_ONCE,
                nextFireAt: null,
              },
              now,
            );
      if (disabled !== null) known = disabled;
    }
    if (claimed === null) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULE_RAN_ONCE',
        'This one-time schedule already ran or its time passed. Delete it or make a new one',
      );
    }
    await this.fire(claimed, known);
    return (await this.store.getFiring(claimed.orgId, claimed.id)) ?? claimed;
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
        trigger.kind === 'once' ||
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
          skipped: null,
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
    if (this.watching.size === 0) return 0;
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
    await this.store.markChecked(firing.orgId, firing.id, now);
    const run = firingRun(firing);
    if (run === null || !this.watching.has(run.runner)) return false;
    const runner = this.runners[run.runner];
    if (runner === undefined) return false;
    const state = await runner.status(
      firing.orgId,
      run.runRef,
      firing.watchUntil,
    );
    if (state === null || ENDED_SCHEDULE_RUN.has(state.status)) {
      return this.endWatch(firing, state, now);
    }
    return this.observe(firing, run.runRef, state.status, now);
  }

  /**
   * A run a runner of this process saw end (a classic turn's answer saved): its actor is told now, not at the next
   * watch, and a firing queued behind it is made due. Never throws.
   */
  async runnerEnded(orgId: string, firingId: string): Promise<void> {
    try {
      await forEnumeratedOrg(orgId, SCHEDULE_JOB, async () => {
        const firing = await this.store.getFiring(orgId, firingId);
        if (firing === null || firing.watchUntil === null) return;
        const now = await this.store.now();
        await this.watchOne(firing, now);
        const run = firingRun(firing);
        if (run !== null) await this.releaseQueued(firing, run.runRef, now);
      });
    } catch (error) {
      this.logger.warn(
        'schedule.run_end_failed',
        harnessLogMeta('schedule.run_end_failed', {
          orgId,
          firingId,
          errorType: errorType(error),
        }),
      );
    }
  }

  /** A firing queued behind the run `runRef` of its schedule (TR-06) is made due for the next tick. */
  private async releaseQueued(
    firing: ScheduleFiring,
    runRef: string,
    now: number,
  ): Promise<void> {
    const queued = await this.store.queuedFiring(
      firing.orgId,
      firing.scheduleId,
    );
    if (queued?.queuedBehind === runRef) {
      await this.store.release(queued.orgId, queued.id, queued.attempts, now);
    }
  }

  /**
   * A scheduled run moved short of an end (UIB5-FU-1: the execution service tells this replica's firer of each run a
   * command moved): it is told now, not at the next watch, which stays the backstop. Never throws.
   */
  async runMoved(run: FiredRun): Promise<void> {
    const status = scheduleRunStatus(run.status);
    if (ENDED_SCHEDULE_RUN.has(status)) return;
    const scheduleId = firedScheduleOf(run);
    if (scheduleId === null) return;
    try {
      const firing = await this.firingOfRun(run, scheduleId);
      if (firing === null || firing.watchUntil === null) return;
      await this.observe(firing, run.runId, status, await this.store.now());
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
    status: ScheduleRunStatus,
    now: number,
  ): Promise<boolean> {
    let notifiedNow = false;
    if (firing.watchUntil === null || firing.watchUntil <= now) return false;
    if (status !== 'waiting_for_you' || firing.notifiedAt !== null)
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
        runner: firing.runner ?? 'harness',
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
      const status = scheduleRunStatus(run.status);
      if (firing !== null && ENDED_SCHEDULE_RUN.has(status)) {
        await this.endWatch(firing, { status }, now);
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
    runner: ScheduleRunner,
    runId: string,
    status: 'completed' | 'failed' | 'cancelled',
  ): Promise<RunDigest | null> {
    try {
      return await runner.summary(firing.orgId, runId, status);
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
    state: ScheduleRunState | null,
    now: number,
  ): Promise<boolean> {
    if (!(await this.store.unwatch(firing.orgId, firing.id, now))) return false;
    const run = firingRun(firing);
    const status = state?.status;
    const runner = run === null ? undefined : this.runners[run.runner];
    if (
      run === null ||
      runner === undefined ||
      (status !== 'completed' && status !== 'failed' && status !== 'cancelled')
    ) {
      return false;
    }
    const schedule = await this.store.get(firing.orgId, firing.scheduleId);
    if (schedule === null || this.notifier === undefined) return false;
    try {
      const channels = await this.channels(schedule);
      if (channels.length === 0) return false;
      const digest = await this.digestOf(firing, runner, run.runRef, status);
      await this.notifier.runEnded({
        orgId: firing.orgId,
        actorId: firing.actorId,
        scheduleId: firing.scheduleId,
        scheduleName: schedule.name,
        ...(schedule.agentKey !== undefined
          ? { agentKey: schedule.agentKey }
          : {}),
        sessionId: firing.sessionId ?? '',
        runId: run.runRef,
        runner: run.runner,
        status,
        ...(state?.reason !== undefined ? { reason: state.reason } : {}),
        ...(firing.costCapIgnored === true ? { costCapIgnored: true } : {}),
        channels,
        ...(digest !== null ? { digest } : {}),
      });
      this.logger.info(
        'schedule.run_end_notified',
        harnessLogMeta('schedule.run_end_notified', {
          ...this.fields(firing),
          runId: run.runRef,
          runner: run.runner,
          status,
          reason: state?.reason,
          channels: channels.join(','),
        }),
      );
      return true;
    } catch (error) {
      this.logger.warn(
        'schedule.notify_failed',
        harnessLogMeta('schedule.notify_failed', {
          ...this.fields(firing),
          runId: run.runRef,
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
      /** Why the slot is skipped as it is claimed; null: it is to fire. */
      skipped: string | null;
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
      status: slot.skipped !== null ? 'skipped' : 'pending',
      runId: null,
      sessionId: null,
      reason: slot.skipped,
      missed: slot.missed,
      attempts: slot.skipped !== null ? 0 : 1,
      leaseUntil: slot.now + FIRING_LEASE_MS,
      createdAt: slot.now,
      settledAt: slot.skipped !== null ? slot.now : null,
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
    // A one-time schedule is disabled by the claim of its slot; that slot's retries still run it.
    const claimedOnce =
      firing.kind === 'once' && schedule.statusReason === RAN_ONCE;
    if (schedule.status === 'disabled' && !claimedOnce) {
      return 'schedule_disabled';
    }
    if (schedule.status === 'paused' && firing.kind !== 'manual') {
      return 'schedule_paused';
    }
    if (!(await this.orgOn(schedule.orgId))) return 'scheduled_runs_disabled';
    // A retaken firing of a one-time slot is as late as its slot, whichever attempt this is.
    if (
      firing.kind === 'once' &&
      (await this.store.now()) - firing.slotAt > MAX_ONCE_LATENESS_MS
    ) {
      return { skipped: ONCE_TOO_LATE };
    }
    if (schedule.input === null) return 'template_missing';
    // A person's run now is theirs to start, and a one-time slot has no other run to overlap; the policy is for
    // what fires repeatedly by itself.
    if (firing.kind !== 'manual' && firing.kind !== 'once') {
      const waiting = await this.overlap(schedule, firing);
      if (waiting !== null) return waiting;
    }
    const runner = await this.fixedRunner(schedule.orgId, firing);
    if (runner === null) return 'runner_unavailable';
    // A classic turn presents the credential for as long as the firing may take; a durable run only at admission.
    const actor = await this.actors.authorize(
      schedule.orgId,
      schedule.actorId,
      runner.kind === 'classic' ? { ttlS: schedule.deadlineS } : undefined,
    );
    if ('refused' in actor) return actor.refused;
    const context = await this.context(schedule, firing, actor.authorization);
    const input = firingInput(schedule, firing.slotAt, context);
    if (input === null) return 'template_missing';
    const agentVersion = firingAgentVersion(schedule);
    const startedAt = await this.store.now();
    const request: ScheduleRunRequest = {
      orgId: schedule.orgId,
      actorId: schedule.actorId,
      scheduleId: schedule.id,
      scheduleName: schedule.name,
      firingId: firing.id,
      ...(schedule.agentKey !== undefined
        ? { agentKey: schedule.agentKey }
        : {}),
      ...(agentVersion !== undefined ? { agentVersion } : {}),
      ...(schedule.codingEngine !== undefined
        ? { codingEngine: schedule.codingEngine }
        : {}),
      idempotencyKey: scheduleIdempotencyKey(schedule.id, firing.slotKey),
      deadlineMs: schedule.deadlineS * 1000,
      deadlineAt: startedAt + schedule.deadlineS * 1000,
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
      ended: () => {
        void this.runnerEnded(firing.orgId, firing.id);
      },
    };
    const { runner: ran, started } = await this.startRun(runner, request);
    if (started.kind === 'refused') return started.reason;
    const now = await this.store.now();
    await this.store.settle(
      firing.orgId,
      firing.id,
      {
        status: 'admitted',
        runner: ran.kind,
        runRef: started.runRef,
        runId: started.runId ?? null,
        sessionId: started.sessionId,
        watchUntil: request.deadlineAt,
        ...(started.costCapIgnored === true ? { costCapIgnored: true } : {}),
      },
      now,
    );
    this.logger.info(
      'schedule.fired',
      harnessLogMeta('schedule.fired', {
        ...this.fields(firing),
        runner: ran.kind,
        runId: started.runRef,
        sessionId: started.sessionId,
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
    if (policy === 'allow' || this.watching.size === 0) return null;
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
    for (const other of await this.store.activeFirings(
      schedule.orgId,
      schedule.id,
      MAX_ACTIVE_CHECKED,
    )) {
      const run = firingRun(other);
      if (other.id === firing.id || run === null) continue;
      const runner = this.runners[run.runner];
      if (runner === undefined || !this.watching.has(run.runner)) continue;
      const state = await runner.status(
        other.orgId,
        run.runRef,
        other.watchUntil,
      );
      if (state !== null && !ENDED_SCHEDULE_RUN.has(state.status)) {
        return run.runRef;
      }
    }
    return null;
  }

  /**
   * The runner a firing starts on (ADR-SCH-02 D2): the durable harness where the org's harness is on now, the classic
   * chat turn where it is off. Null when this deployment has neither for it.
   */
  /**
   * The runner this firing starts on: the org's path at its first attempt, fixed on the firing before it starts, so
   * a retake after a crash starts on the same one even if the org's path changed since (one firing, one run).
   */
  private async fixedRunner(
    orgId: string,
    firing: ScheduleFiring,
  ): Promise<ScheduleRunner | null> {
    const chosen =
      firing.runner !== undefined
        ? (this.runners[firing.runner] ?? null)
        : await this.runnerFor(orgId);
    if (chosen === null) return null;
    const fixed = await this.store.fixRunner(orgId, firing.id, chosen.kind);
    return fixed === null ? chosen : (this.runners[fixed] ?? null);
  }

  private async runnerFor(orgId: string): Promise<ScheduleRunner | null> {
    let durable = true;
    if (this.harnessOn !== undefined) {
      try {
        durable = await this.harnessOn(orgId);
      } catch {
        durable = false;
      }
    }
    const preferred = durable ? this.runners.harness : this.runners.classic;
    return preferred ?? this.runners.classic ?? null;
  }

  /**
   * Starts the run. A durable admission that routes the turn to the classic path (the operator's stop, a store not
   * ready) runs it as the classic turn a chat would get then, rather than refusing it.
   */
  private async startRun(
    runner: ScheduleRunner,
    request: ScheduleRunRequest,
  ): Promise<{ runner: ScheduleRunner; started: ScheduleRunStart }> {
    const started = await runner.start(request);
    const classic = this.runners.classic;
    if (
      runner.kind === 'harness' &&
      started.kind === 'refused' &&
      started.reason === 'rollout_legacy' &&
      classic !== undefined
    ) {
      // The harness admitted nothing, so the firing is handed to classic before it starts there: a retake then
      // starts on classic too, never a second run on the harness.
      await this.store.fixRunner(
        request.orgId,
        request.firingId,
        'classic',
        'harness',
      );
      // The harness's credential was minted for admission only; a classic turn presents its own for as long as
      // the firing may take, so the person is authorized again for that.
      const actor = await this.actors.authorize(
        request.orgId,
        request.actorId,
        {
          ttlS: Math.ceil(request.deadlineMs / 1000),
        },
      );
      if ('refused' in actor) {
        return {
          runner: classic,
          started: { kind: 'refused', reason: actor.refused },
        };
      }
      return {
        runner: classic,
        started: await classic.start({
          ...request,
          authorization: actor.authorization,
        }),
      };
    }
    return { runner, started };
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
    if (reason === ONCE_TOO_LATE)
      await this.tellRefused(firing, ONCE_TOO_LATE, now);
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
   * schedule, read from its own firings, so every scheduler instance agrees;
   * and only while the org runs schedules (its harness and Labs), so an org
   * that turned them off hears nothing of them. The firing is settled either way.
   */
  private async tellRefused(
    firing: ScheduleFiring,
    reason: string,
    now: number,
  ): Promise<void> {
    if (this.notifier === undefined) return;
    if (!(await this.orgOn(firing.orgId))) return;
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

  /** Whether the org has scheduling (ADR-SCH-02 D1): Labs scheduled runs alone; its harness picks the runner. */
  private async orgOn(orgId: string): Promise<boolean> {
    return this.enabled(orgId);
  }

  private enabled(orgId: string): Promise<boolean> {
    return orgSchedulesOn(orgId, (org) => this.setting.enabled(org));
  }
}

/** The durable runner the options give, or make of today's admission, status and digest readers. */
function harnessRunnerOf(options: ScheduleFirerOptions): ScheduleRunner | null {
  if (options.harness !== undefined) return options.harness;
  if (options.admission === undefined) return null;
  return new HarnessScheduleRunner(
    options.admission,
    options.runs ?? { status: () => Promise.resolve(null) },
    options.digests ?? null,
  );
}
