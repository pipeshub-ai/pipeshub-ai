/**
 * P8a schedules on PostgreSQL (Alembic head, row-level security, PgBouncer in transaction mode): the schedule store
 * contract (ST), the firing state machine, the store races (SK-1, SK-2, ST-9) on separate instances, and the
 * PostgreSQL cases: a cron claim whose slot was taken moves no cursor, racing sweepers retake a stale firing once,
 * racing first attempts fix one trigger context, and another organisation sees nothing.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgScheduleStore } from '../../../src/libs/repositories/agent-harness/schedules/pg';
import { fixedPgTime } from '../../../src/libs/repositories/agent-harness/time/pg';
import { RunInputSeal } from '../../../src/modules/agent-harness/application/run-input-seal';
import { testCipher } from './support/data-protection';
import { describeScheduleFiringMachine } from './support/schedule-firing-machine';
import {
  describeScheduleStoreContract,
  describeScheduleStoreRaces,
} from './support/schedule-store-contract';
import {
  aFiring,
  aSchedule,
  ORG,
  OTHER_ORG,
  T0,
  TestClock,
} from './support/schedules';
import {
  PgStack,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  scopedStore,
  startHarnessPg,
} from './support/harness-pg';

const LIMITS = { perActor: 50, perOrg: 500 };
/** Deployment-wide calls name no org: they run as the jobs role, not as the org of the call before. */
const CROSS_ORG = new Set([
  'now',
  'dueCron',
  'takeStale',
  'watched',
  'hasActive',
]);

const scopedSchedules = (raw: PgScheduleStore): PgScheduleStore => {
  const scoped = scopedStore(raw);
  return new Proxy(raw, {
    get: (target, prop, receiver) =>
      typeof prop === 'string' && CROSS_ORG.has(prop)
        ? Reflect.get(target, prop, receiver)
        : Reflect.get(scoped, prop),
  });
};

for (const bin of pgBins()) {
  pgSuite(
    `agent harness schedules on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(300_000);
      let stack: PgStack;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_schedules');
      });
      after(async () => {
        await stack?.stop();
      });

      const store = (clock: TestClock, sealed = true) =>
        scopedSchedules(
          new PgScheduleStore(
            fixedPgTime(() => clock.now()),
            sealed ? new RunInputSeal(testCipher()) : undefined,
          ),
        );

      describeScheduleStoreContract('postgres', async () => {
        await resetHarnessPg(stack);
        const clock = new TestClock();
        // Sealed as production stores a template (GAPS-1): every contract case reads it back through the seal.
        return { store: store(clock), clock };
      });

      describeScheduleFiringMachine(
        'postgres',
        async (clock) => {
          await resetHarnessPg(stack);
          return store(clock, false);
        },
        { ci: 15, nightly: 300 },
      );

      describeScheduleStoreRaces('postgres', async (clock) => {
        await resetHarnessPg(stack);
        return () => store(clock);
      });

      describe('PostgreSQL schedule invariants', () => {
        beforeEach(() => resetHarnessPg(stack));

        it('a cron claim whose slot already has a firing moves no cursor (the unit rolls back)', async () => {
          const clock = new TestClock();
          const s = store(clock);
          const schedule = await s.create(aSchedule(), LIMITS);
          const due = schedule.nextFireAt as number;
          const firing = aFiring(schedule, {
            slotKey: `cron:${String(due)}`,
            kind: 'cron',
            slotAt: due,
          });
          expect((await s.claimSlot(firing)).created).to.equal(true);
          expect(
            await s.claimCronSlot(
              { ...firing, id: 'fir-other' },
              due,
              due + 3_600_000,
            ),
          ).to.equal(null);
          expect((await s.get(ORG, schedule.id))?.nextFireAt).to.equal(due);
          expect(
            await s.claimCronSlot(
              { ...firing, id: 'fir-x', slotKey: 'cron:x' },
              due + 1,
              due + 2,
            ),
          ).to.equal(null);
        });

        it('racing cron claims of one due slot on separate instances: one firing, the cursor moved once', async () => {
          const clock = new TestClock();
          const schedule = await store(clock).create(aSchedule(), LIMITS);
          const due = schedule.nextFireAt as number;
          const claims = await Promise.all(
            Array.from({ length: 8 }, (_, i) =>
              store(clock).claimCronSlot(
                aFiring(schedule, {
                  id: `fir-race-${String(i)}`,
                  slotKey: `cron:${String(due)}`,
                  kind: 'cron',
                  slotAt: due,
                }),
                due,
                due + 3_600_000,
              ),
            ),
          );
          expect(claims.filter((c) => c !== null)).to.have.length(1);
          expect(
            await store(clock).firings(ORG, schedule.id, 10),
          ).to.have.length(1);
          expect(
            (await store(clock).get(ORG, schedule.id))?.nextFireAt,
          ).to.equal(due + 3_600_000);
        });

        it('racing sweepers retake each stale firing once (FOR UPDATE SKIP LOCKED and an attempts guard)', async () => {
          const clock = new TestClock();
          const schedule = await store(clock).create(aSchedule(), LIMITS);
          const ids: string[] = [];
          for (let i = 0; i < 12; i += 1) {
            const { firing } = await store(clock).claimSlot(
              aFiring(schedule, { leaseUntil: T0 + i }),
            );
            ids.push(firing.id);
          }
          const taken = await Promise.all(
            Array.from({ length: 6 }, () =>
              new PgScheduleStore().takeStale(T0 + 1_000, 60_000, 5),
            ),
          );
          const all = taken.flat().map((f) => f.id);
          expect(new Set(all).size).to.equal(all.length);
          expect(all.length).to.equal(12);
          for (const f of taken.flat()) expect(f.attempts).to.equal(2);
          expect(
            await new PgScheduleStore().takeStale(T0 + 1_000, 60_000, 50),
          ).to.deep.equal([]);
        });

        it('racing first attempts fix one trigger context; every one reads that one back', async () => {
          const clock = new TestClock();
          const schedule = await store(clock).create(aSchedule(), LIMITS);
          const { firing } = await store(clock).claimSlot(aFiring(schedule));
          const contexts = await Promise.all(
            Array.from({ length: 6 }, (_, i) =>
              store(clock).fixContext(ORG, firing.id, {
                kind: 'manual',
                firedBy: `u${String(i)}`,
              } as never),
            ),
          );
          expect(new Set(contexts.map((c) => JSON.stringify(c))).size).to.equal(
            1,
          );
        });

        it('another organisation bound to the unit reads, deletes and fires nothing of this one; the due scan is deployment-wide', async () => {
          const clock = new TestClock();
          const raw = new PgScheduleStore(fixedPgTime(() => clock.now()));
          const schedule = await inOrg(ORG, () =>
            raw.create(aSchedule(), LIMITS),
          );
          expect(
            await inOrg(OTHER_ORG, () =>
              raw.get(ORG, schedule.id).catch((e: Error) => e),
            ),
          ).to.be.instanceOf(Error);
          expect(
            await inOrg(OTHER_ORG, () =>
              raw.delete(ORG, schedule.id).catch((e: Error) => e),
            ),
          ).to.be.instanceOf(Error);
          expect(
            await inOrg(OTHER_ORG, () => raw.list(OTHER_ORG, {}, 10)),
          ).to.deep.equal([]);
          expect(
            await inOrg(OTHER_ORG, () => raw.get(null, schedule.id)),
          ).to.deep.equal(schedule);
          expect(
            (await raw.dueCron(schedule.nextFireAt as number, 10)).map(
              (s) => s.id,
            ),
          ).to.deep.equal([schedule.id]);
        });
      });
    },
  );
}
