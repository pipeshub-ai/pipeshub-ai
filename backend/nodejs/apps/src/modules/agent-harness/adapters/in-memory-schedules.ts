/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import { HarnessScheduleRefusedError } from '../domain/errors';
import {
  type AgentSchedule,
  type AgentVersionPin,
  type EventTriggerKind,
  type FiringOutcome,
  type RunTriggerContext,
  type ScheduleFiring,
  type ScheduleStatus,
  WATCH_GRACE_MS,
} from '../domain/schedules';
import type {
  ScheduleEraser,
  ScheduleLimits,
  ScheduleStore,
} from '../ports/schedules';

const copy = <T>(value: T): T => structuredClone(value);
const slotOf = (
  f: Pick<ScheduleFiring, 'orgId' | 'scheduleId' | 'slotKey'>,
): string => JSON.stringify([f.orgId, f.scheduleId, f.slotKey]);

/** The reference schedule store; the Mongo store passes the same contract. */
export class InMemoryScheduleStore implements ScheduleStore, ScheduleEraser {
  private readonly schedules = new Map<string, AgentSchedule>();
  private readonly firingsById = new Map<string, ScheduleFiring>();
  private readonly slots = new Map<string, string>();
  private readonly checkedAt = new Map<string, number>();
  private readonly deliveries = new Map<string, number>();

  constructor(private readonly clock: { now(): number } = Date) {}

  async now(): Promise<number> {
    return this.clock.now();
  }

  async create(
    schedule: AgentSchedule,
    limits: ScheduleLimits,
  ): Promise<AgentSchedule> {
    const org = [...this.schedules.values()].filter(
      (s) => s.orgId === schedule.orgId,
    );
    if (
      org.length >= limits.perOrg ||
      org.filter((s) => s.actorId === schedule.actorId).length >=
        limits.perActor
    ) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULE_LIMIT',
        'No more schedules may be created',
      );
    }
    this.schedules.set(schedule.id, copy(schedule));
    return copy(schedule);
  }

  async get(orgId: string | null, id: string): Promise<AgentSchedule | null> {
    const found = this.schedules.get(id);
    return found !== undefined && (orgId === null || found.orgId === orgId)
      ? copy(found)
      : null;
  }

  async list(
    orgId: string,
    filter: { actorId?: string },
    limit: number,
  ): Promise<AgentSchedule[]> {
    return [...this.schedules.values()]
      .filter(
        (s) =>
          s.orgId === orgId &&
          (filter.actorId === undefined || s.actorId === filter.actorId),
      )
      .sort((a, b) =>
        b.createdAt !== a.createdAt
          ? b.createdAt - a.createdAt
          : a.id < b.id
            ? 1
            : -1,
      )
      .slice(0, limit)
      .map(copy);
  }

  async setStatus(
    orgId: string,
    id: string,
    change: {
      from: readonly ScheduleStatus[];
      to: ScheduleStatus;
      reason: string | null;
      nextFireAt: number | null;
    },
    now: number,
  ): Promise<AgentSchedule | null> {
    const found = this.schedules.get(id);
    if (found?.orgId !== orgId || !change.from.includes(found.status)) {
      return null;
    }
    const next: AgentSchedule = {
      ...found,
      status: change.to,
      statusReason: change.reason,
      nextFireAt: change.nextFireAt,
      updatedAt: now,
    };
    this.schedules.set(id, next);
    return copy(next);
  }

  async setAgentVersion(
    orgId: string,
    id: string,
    agentVersion: AgentVersionPin,
    now: number,
  ): Promise<AgentSchedule | null> {
    const found = this.schedules.get(id);
    if (
      found?.orgId !== orgId ||
      found.agentKey === undefined ||
      found.status === 'disabled'
    ) {
      return null;
    }
    const next: AgentSchedule = { ...found, agentVersion, updatedAt: now };
    this.schedules.set(id, next);
    return copy(next);
  }

  async delete(orgId: string, id: string): Promise<boolean> {
    const found = this.schedules.get(id);
    if (found?.orgId !== orgId) return false;
    this.schedules.delete(id);
    for (const firing of [...this.firingsById.values()]) {
      if (firing.orgId === orgId && firing.scheduleId === id) {
        this.firingsById.delete(firing.id);
        this.slots.delete(slotOf(firing));
      }
    }
    return true;
  }

  async dueCron(now: number, limit: number): Promise<AgentSchedule[]> {
    return [...this.schedules.values()]
      .filter(
        (s) =>
          s.status === 'active' &&
          s.trigger.kind === 'cron' &&
          s.nextFireAt !== null &&
          s.nextFireAt <= now,
      )
      .sort((a, b) => (a.nextFireAt as number) - (b.nextFireAt as number))
      .slice(0, limit)
      .map(copy);
  }

  async claimCronSlot(
    firing: ScheduleFiring,
    expectedNextFireAt: number,
    nextFireAt: number | null,
  ): Promise<ScheduleFiring | null> {
    const schedule = this.schedules.get(firing.scheduleId);
    if (
      schedule?.orgId !== firing.orgId ||
      schedule.status !== 'active' ||
      schedule.nextFireAt !== expectedNextFireAt ||
      this.slots.has(slotOf(firing))
    ) {
      return null;
    }
    this.schedules.set(schedule.id, {
      ...schedule,
      nextFireAt,
      updatedAt: firing.createdAt,
    });
    this.insert(firing);
    return copy(firing);
  }

  async claimSlot(
    firing: ScheduleFiring,
  ): Promise<{ firing: ScheduleFiring; created: boolean }> {
    const existing = this.slots.get(slotOf(firing));
    if (existing !== undefined) {
      return {
        firing: copy(this.firingsById.get(existing) as ScheduleFiring),
        created: false,
      };
    }
    this.insert(firing);
    return { firing: copy(firing), created: true };
  }

  private insert(firing: ScheduleFiring): void {
    this.firingsById.set(firing.id, copy(firing));
    this.slots.set(slotOf(firing), firing.id);
  }

  async eventSchedules(
    orgId: string,
    kind: EventTriggerKind,
    connectorId?: string,
  ): Promise<AgentSchedule[]> {
    return [...this.schedules.values()]
      .filter(
        (s) =>
          s.orgId === orgId &&
          s.status === 'active' &&
          s.trigger.kind === kind &&
          (s.trigger.kind !== 'connector' ||
            s.trigger.connectorId === connectorId),
      )
      .map(copy);
  }

  async hasActive(kind: EventTriggerKind): Promise<boolean> {
    return [...this.schedules.values()].some(
      (s) => s.status === 'active' && s.trigger.kind === kind,
    );
  }

  async settle(
    orgId: string,
    firingId: string,
    outcome: FiringOutcome,
    now: number,
  ): Promise<boolean> {
    const firing = this.firingsById.get(firingId);
    if (firing?.orgId !== orgId || firing.status !== 'pending') {
      return false;
    }
    this.firingsById.set(firingId, {
      ...firing,
      status: outcome.status,
      runId: outcome.status === 'admitted' ? outcome.runId : null,
      sessionId: outcome.status === 'admitted' ? outcome.sessionId : null,
      reason: outcome.status === 'admitted' ? null : outcome.reason,
      watchUntil: outcome.status === 'admitted' ? outcome.watchUntil : null,
      settledAt: now,
    });
    return true;
  }

  async release(
    orgId: string,
    firingId: string,
    attempts: number,
    leaseUntil: number,
  ): Promise<void> {
    const firing = this.firingsById.get(firingId);
    if (
      firing?.orgId !== orgId ||
      firing.status !== 'pending' ||
      firing.attempts !== attempts
    ) {
      return;
    }
    this.firingsById.set(firingId, { ...firing, leaseUntil });
  }

  async takeStale(
    now: number,
    leaseMs: number,
    limit: number,
  ): Promise<ScheduleFiring[]> {
    const stale = [...this.firingsById.values()]
      .filter((f) => f.status === 'pending' && f.leaseUntil <= now)
      .sort((a, b) => a.leaseUntil - b.leaseUntil)
      .slice(0, limit);
    return stale.map((firing) => {
      const taken = {
        ...firing,
        attempts: firing.attempts + 1,
        leaseUntil: now + leaseMs,
      };
      this.firingsById.set(firing.id, taken);
      return copy(taken);
    });
  }

  async firings(
    orgId: string,
    scheduleId: string,
    limit: number,
  ): Promise<ScheduleFiring[]> {
    return [...this.firingsById.values()]
      .filter((f) => f.orgId === orgId && f.scheduleId === scheduleId)
      .sort((a, b) =>
        b.slotAt !== a.slotAt ? b.slotAt - a.slotAt : b.createdAt - a.createdAt,
      )
      .slice(0, limit)
      .map(copy);
  }

  async getFiring(
    orgId: string,
    firingId: string,
  ): Promise<ScheduleFiring | null> {
    const firing = this.firingsById.get(firingId);
    return firing?.orgId === orgId ? copy(firing) : null;
  }

  async firingBySlot(
    orgId: string,
    scheduleId: string,
    slotKey: string,
  ): Promise<ScheduleFiring | null> {
    const id = this.slots.get(slotOf({ orgId, scheduleId, slotKey }));
    const firing = id === undefined ? undefined : this.firingsById.get(id);
    return firing === undefined ? null : copy(firing);
  }

  async watched(now: number, limit: number): Promise<ScheduleFiring[]> {
    const checked = (f: ScheduleFiring): number =>
      this.checkedAt.get(f.id) ?? -Infinity;
    return [...this.firingsById.values()]
      .filter(
        (f) =>
          f.status === 'admitted' &&
          f.watchUntil !== null &&
          f.watchUntil > now - WATCH_GRACE_MS,
      )
      .sort((a, b) => checked(a) - checked(b))
      .slice(0, limit)
      .map(copy);
  }

  async markChecked(
    orgId: string,
    firingId: string,
    now: number,
  ): Promise<void> {
    if (this.firingsById.get(firingId)?.orgId === orgId) {
      this.checkedAt.set(firingId, now);
    }
  }

  async markNotified(
    orgId: string,
    firingId: string,
    now: number,
  ): Promise<boolean> {
    const firing = this.firingsById.get(firingId);
    if (firing?.orgId !== orgId || firing.notifiedAt !== null) {
      return false;
    }
    this.firingsById.set(firingId, { ...firing, notifiedAt: now });
    return true;
  }

  async unwatch(
    orgId: string,
    firingId: string,
    now: number,
  ): Promise<boolean> {
    const firing = this.firingsById.get(firingId);
    if (firing?.orgId !== orgId || firing.watchUntil === null) return false;
    this.firingsById.set(firingId, {
      ...firing,
      watchUntil: null,
      watchEndedAt: now,
    });
    return true;
  }

  async activeFirings(
    orgId: string,
    scheduleId: string,
    limit: number,
  ): Promise<ScheduleFiring[]> {
    return [...this.firingsById.values()]
      .filter(
        (f) =>
          f.orgId === orgId &&
          f.scheduleId === scheduleId &&
          f.status === 'admitted' &&
          f.watchUntil !== null,
      )
      .sort((a, b) => b.slotAt - a.slotAt)
      .slice(0, limit)
      .map(copy);
  }

  async queuedFiring(
    orgId: string,
    scheduleId: string,
  ): Promise<ScheduleFiring | null> {
    const found = [...this.firingsById.values()].find(
      (f) =>
        f.orgId === orgId &&
        f.scheduleId === scheduleId &&
        f.status === 'pending' &&
        f.queuedBehind !== undefined,
    );
    return found === undefined ? null : copy(found);
  }

  async requeue(
    orgId: string,
    firingId: string,
    attempts: number,
    runId: string,
    leaseUntil: number,
  ): Promise<boolean> {
    const firing = this.firingsById.get(firingId);
    if (
      firing?.orgId !== orgId ||
      firing.status !== 'pending' ||
      firing.attempts !== attempts
    ) {
      return false;
    }
    this.firingsById.set(firingId, {
      ...firing,
      queuedBehind: runId,
      reason: 'overlap_queued',
      leaseUntil,
      attempts: attempts > 1 ? attempts - 1 : attempts,
    });
    return true;
  }

  async fixContext(
    orgId: string,
    firingId: string,
    context: RunTriggerContext,
  ): Promise<RunTriggerContext> {
    const firing = this.firingsById.get(firingId);
    if (firing?.orgId !== orgId) throw new Error('No such firing');
    if (firing.context !== undefined) return copy(firing.context);
    this.firingsById.set(firingId, { ...firing, context: copy(context) });
    return copy(context);
  }

  async countDelivery(
    orgId: string,
    scheduleId: string,
    windowStart: number,
  ): Promise<number> {
    const key = JSON.stringify([orgId, scheduleId, windowStart]);
    const count = (this.deliveries.get(key) ?? 0) + 1;
    this.deliveries.set(key, count);
    return count;
  }

  async firstDelivery(
    orgId: string,
    scheduleId: string,
    nonce: string,
  ): Promise<boolean> {
    const key = JSON.stringify(['seen', orgId, scheduleId, nonce]);
    if (this.deliveries.has(key)) return false;
    this.deliveries.set(key, 1);
    return true;
  }

  async eraseUser(orgId: string, userId: string): Promise<number> {
    return this.removeAll((s) => s.orgId === orgId && s.actorId === userId);
  }

  async eraseOrg(orgId: string): Promise<number> {
    return this.removeAll((s) => s.orgId === orgId);
  }

  private async removeAll(
    match: (s: AgentSchedule) => boolean,
  ): Promise<number> {
    let count = 0;
    for (const schedule of [...this.schedules.values()]) {
      if (match(schedule) && (await this.delete(schedule.orgId, schedule.id)))
        count += 1;
    }
    return count;
  }

  async eraseAgent(orgId: string, agentKey: string): Promise<number> {
    return this.disable(
      (s) => s.orgId === orgId && s.agentKey === agentKey,
      this.clock.now(),
      'agent_deleted',
    );
  }

  async pauseUser(
    orgId: string,
    userId: string,
    reason: string,
  ): Promise<number> {
    const now = this.clock.now();
    let count = 0;
    for (const schedule of this.schedules.values()) {
      if (
        schedule.orgId !== orgId ||
        schedule.actorId !== userId ||
        schedule.status !== 'active'
      )
        continue;
      this.schedules.set(schedule.id, {
        ...schedule,
        status: 'paused',
        statusReason: reason,
        nextFireAt: null,
        updatedAt: now,
      });
      count += 1;
    }
    return count;
  }

  private disable(
    match: (s: AgentSchedule) => boolean,
    now: number,
    reason: string,
  ): number {
    let count = 0;
    for (const schedule of this.schedules.values()) {
      if (!match(schedule)) continue;
      this.schedules.set(schedule.id, {
        ...schedule,
        status: 'disabled',
        statusReason: reason,
        input: null,
        nextFireAt: null,
        webhookTokenHash: null,
        webhookSigningKey: null,
        updatedAt: now,
      });
      count += 1;
    }
    return count;
  }
}
