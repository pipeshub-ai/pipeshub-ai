/**
 * QA13-10: a schedule's firings and status as a state machine. Slots are
 * claimed (manual, and cron with the cursor CAS), settled, released for a
 * retry, retaken when their lease passes, and schedules paused, resumed and
 * disabled, in any order, on the store's clock. After every operation the
 * store must hold what the pure model says: one firing per slot, a settled
 * firing never changes again, attempts only rise, a release under a stale
 * attempt changes nothing, a status move is a CAS from the statuses named.
 */
import { expect } from 'chai';
import fc from 'fast-check';
import {
  type AgentSchedule,
  FIRING_LEASE_MS,
  type FiringOutcome,
  type FiringStatus,
  type ScheduleFiring,
  type ScheduleStatus,
} from '../../../../src/modules/agent-harness/domain/schedules';
import type { ScheduleStore } from '../../../../src/modules/agent-harness/ports/schedules';
import { propertyRuns } from '../../../support/harness/property-profile';
import { aFiring, aSchedule, T0, TestClock } from './schedules';

const HOUR = 3_600_000;
const STATUSES: ScheduleStatus[] = ['active', 'paused', 'disabled'];

type Op =
  | { kind: 'claim'; schedule: number; slot: number }
  | { kind: 'cron'; schedule: number; stale: boolean }
  | { kind: 'settle'; firing: number; outcome: FiringOutcome['status'] }
  | { kind: 'release'; firing: number; stale: boolean; backoffMs: number }
  | { kind: 'take_stale' }
  | { kind: 'status'; schedule: number; from: ScheduleStatus[]; to: ScheduleStatus }
  | { kind: 'advance'; ms: number };

const opArbitrary: fc.Arbitrary<Op> = fc.oneof(
  fc.record({ kind: fc.constant('claim' as const), schedule: fc.nat(1), slot: fc.nat(3) }),
  fc.record({ kind: fc.constant('cron' as const), schedule: fc.nat(1), stale: fc.boolean() }),
  fc.record({
    kind: fc.constant('settle' as const),
    firing: fc.nat(7),
    outcome: fc.constantFrom<FiringOutcome['status']>('admitted', 'refused', 'skipped'),
  }),
  fc.record({
    kind: fc.constant('release' as const),
    firing: fc.nat(7),
    stale: fc.boolean(),
    backoffMs: fc.constantFrom(1_000, 30_000, 120_000),
  }),
  fc.constant<Op>({ kind: 'take_stale' }),
  fc.record({
    kind: fc.constant('status' as const),
    schedule: fc.nat(1),
    from: fc.subarray(STATUSES, { minLength: 1 }),
    to: fc.constantFrom(...STATUSES),
  }),
  fc.record({ kind: fc.constant('advance' as const), ms: fc.constantFrom(10_000, FIRING_LEASE_MS, HOUR) }),
);

interface ModelFiring {
  id: string;
  scheduleAt: number;
  slotKey: string;
  status: FiringStatus;
  attempts: number;
  leaseUntil: number;
}

interface ModelSchedule {
  status: ScheduleStatus;
  nextFireAt: number;
}

const outcomeOf = (status: FiringOutcome['status'], now: number): FiringOutcome =>
  status === 'admitted'
    ? { status, runner: 'harness', runRef: 'run-1', runId: 'run-1', sessionId: 'ses-1', watchUntil: now + HOUR }
    : { status, reason: 'qa13' };

const view = (f: ScheduleFiring) => [f.id, f.slotKey, f.status, f.attempts, f.leaseUntil] as const;

class FiringMachine {
  readonly firings: ModelFiring[] = [];
  readonly model: ModelSchedule[];

  constructor(
    readonly store: ScheduleStore,
    readonly clock: TestClock,
    readonly schedules: AgentSchedule[],
  ) {
    this.model = schedules.map((s) => ({ status: s.status, nextFireAt: s.nextFireAt as number }));
  }

  private firing(index: number): ModelFiring | undefined {
    return this.firings.length === 0 ? undefined : this.firings[index % this.firings.length];
  }

  async apply(op: Op): Promise<void> {
    const now = this.clock.now();
    switch (op.kind) {
      case 'claim': {
        const schedule = this.schedules[op.schedule]!;
        const slotKey = `manual:${String(op.slot)}`;
        const existing = this.firings.find((f) => f.scheduleAt === op.schedule && f.slotKey === slotKey);
        const firing = aFiring(schedule, { slotKey, attempts: 1, leaseUntil: now + FIRING_LEASE_MS, createdAt: now });
        const claimed = await this.store.claimSlot(firing);
        expect(claimed.created, `${slotKey} claimed once`).to.equal(existing === undefined);
        if (existing === undefined) {
          this.firings.push({
            id: firing.id,
            scheduleAt: op.schedule,
            slotKey,
            status: 'pending',
            attempts: 1,
            leaseUntil: firing.leaseUntil,
          });
        } else {
          expect(claimed.firing.id).to.equal(existing.id);
        }
        return;
      }
      case 'cron': {
        const schedule = this.schedules[op.schedule]!;
        const cursor = this.model[op.schedule]!;
        const expected = op.stale ? cursor.nextFireAt - HOUR : cursor.nextFireAt;
        const slotKey = `cron:${String(expected)}`;
        const firing = aFiring(schedule, { kind: 'cron', slotKey, slotAt: expected, leaseUntil: now + FIRING_LEASE_MS, createdAt: now });
        const claimed = await this.store.claimCronSlot(firing, expected, expected + HOUR);
        const may =
          cursor.status === 'active' &&
          !op.stale &&
          !this.firings.some((f) => f.scheduleAt === op.schedule && f.slotKey === slotKey);
        expect(claimed !== null, `cron slot ${slotKey}`).to.equal(may);
        if (may) {
          cursor.nextFireAt = expected + HOUR;
          this.firings.push({
            id: firing.id,
            scheduleAt: op.schedule,
            slotKey,
            status: 'pending',
            attempts: 1,
            leaseUntil: firing.leaseUntil,
          });
        }
        return;
      }
      case 'settle': {
        const target = this.firing(op.firing);
        if (target === undefined) return;
        const settled = await this.store.settle(this.schedules[0]!.orgId, target.id, outcomeOf(op.outcome, now), now);
        expect(settled, `settle ${target.id} (${target.status})`).to.equal(target.status === 'pending');
        if (settled) target.status = op.outcome;
        return;
      }
      case 'release': {
        const target = this.firing(op.firing);
        if (target === undefined) return;
        const attempts = op.stale ? target.attempts - 1 : target.attempts;
        await this.store.release(this.schedules[0]!.orgId, target.id, attempts, now + op.backoffMs);
        if (target.status === 'pending' && !op.stale) target.leaseUntil = now + op.backoffMs;
        return;
      }
      case 'take_stale': {
        const taken = await this.store.takeStale(now, FIRING_LEASE_MS, 50);
        const due = this.firings.filter((f) => f.status === 'pending' && f.leaseUntil <= now);
        expect(taken.map((f) => f.id).sort()).to.deep.equal(due.map((f) => f.id).sort());
        for (const firing of due) {
          firing.attempts += 1;
          firing.leaseUntil = now + FIRING_LEASE_MS;
        }
        return;
      }
      case 'status': {
        const cursor = this.model[op.schedule]!;
        const moved = await this.store.setStatus(
          this.schedules[op.schedule]!.orgId,
          this.schedules[op.schedule]!.id,
          { from: op.from, to: op.to, reason: op.to === 'active' ? null : 'by_actor', nextFireAt: cursor.nextFireAt },
          now,
        );
        const may = op.from.includes(cursor.status);
        expect(moved !== null, `status ${cursor.status} -> ${op.to} from ${op.from.join('/')}`).to.equal(may);
        if (may) cursor.status = op.to;
        return;
      }
      case 'advance':
        this.clock.advance(op.ms);
        return;
      default:
        return;
    }
  }

  async agree(): Promise<void> {
    for (const [at, schedule] of this.schedules.entries()) {
      const stored = await this.store.get(schedule.orgId, schedule.id);
      expect([stored?.status, stored?.nextFireAt]).to.deep.equal([this.model[at]!.status, this.model[at]!.nextFireAt]);
      const firings = await this.store.firings(schedule.orgId, schedule.id, 100);
      const slots = firings.map((f) => f.slotKey);
      expect(new Set(slots).size, 'one firing per slot').to.equal(slots.length);
      expect(firings.map(view).sort()).to.deep.equal(
        this.firings
          .filter((f) => f.scheduleAt === at)
          .map((f) => [f.id, f.slotKey, f.status, f.attempts, f.leaseUntil] as const)
          .sort(),
      );
    }
  }
}

/** The machine against `make()`'s store; the Mongo twin runs fewer, longer examples. */
export function describeScheduleFiringMachine(
  name: string,
  make: (clock: TestClock) => Promise<ScheduleStore>,
  runs: { ci: number; nightly: number } = { ci: 150, nightly: 5_000 },
): void {
  describe(`QA13-10 schedule firings as a state machine: ${name}`, function () {
    this.timeout(1_800_000);

    it('every operation leaves the store as the model says', async () => {
      await fc.assert(
        fc.asyncProperty(fc.array(opArbitrary, { maxLength: 40, size: 'large' }), async (ops) => {
          const clock = new TestClock(T0);
          const store = await make(clock);
          const limits = { perActor: 50, perOrg: 500 };
          const schedules = [
            await store.create(aSchedule({ nextFireAt: T0 + HOUR }), limits),
            await store.create(aSchedule({ nextFireAt: T0 + 2 * HOUR }), limits),
          ];
          const machine = new FiringMachine(store, clock, schedules);
          for (const op of ops) {
            await machine.apply(op);
            await machine.agree();
          }
        }),
        propertyRuns(runs.ci, runs.nightly),
      );
    });
  });
}
