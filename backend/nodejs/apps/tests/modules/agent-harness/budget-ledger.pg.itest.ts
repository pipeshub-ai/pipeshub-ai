/**
 * P6.5 on PostgreSQL (`harness.budget_accounts`, `budget_reservations`; row-level security, PgBouncer in transaction
 * mode): the budget ledger contract, and the invariants the PostgreSQL design owns: two Node replicas share one cap
 * under concurrent reservers, a reservation expires on database time and a late commit still charges, a key's
 * retries answer the first reservation, a child admitted in an aborted unit leaves no account, and another org sees
 * nothing.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgBudgetLedger } from '../../../src/libs/repositories/agent-harness/budget-ledger/pg';
import { fixedPgTime } from '../../../src/libs/repositories/agent-harness/time/pg';
import { activeUnit } from '../../../src/libs/db';
import {
  budgetCode,
  describeBudgetLedgerContract,
  ORG,
} from './support/budget-ledger-contract';
import {
  PgStack,
  hexOrg,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  scopedStore,
  startHarnessPg,
  transactAs,
} from './support/harness-pg';

const BUDGET_ARGS = {
  account: 0,
  ensure: 0,
  openRoot: 0,
  openChild: 0,
  commit: 0,
  reconcile: 0,
  held: 0,
  release: 0,
  remaining: 0,
  close: 0,
};

for (const bin of pgBins()) {
  pgSuite(
    `budget ledger on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(180_000);
      let stack: PgStack;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_ledgers');
      });
      after(async () => {
        await stack?.stop();
      });

      describeBudgetLedgerContract('postgres', async () => {
        await resetHarnessPg(stack);
        let now = Date.UTC(2026, 9, 3);
        return {
          ledger: scopedStore(
            new PgBudgetLedger(fixedPgTime(() => now)),
            BUDGET_ARGS,
          ),
          advance: (seconds: number) => {
            now += seconds * 1000;
          },
        };
      });

      describe('PostgreSQL budget ledger invariants', () => {
        beforeEach(() => resetHarnessPg(stack));
        const reserve = (
          runId: string,
          key: string,
          usd: number,
          minimum = usd,
        ) => ({
          orgId: ORG,
          runId,
          key,
          requested: { usdMicros: usd, tokens: 0 },
          minimum: { usdMicros: minimum, tokens: 0 },
          ttlMs: 60_000,
        });

        it('NL-2b: two store instances (two Node replicas) share one cap under concurrent reservers', async () => {
          const first = scopedStore(new PgBudgetLedger(), BUDGET_ARGS);
          const second = scopedStore(new PgBudgetLedger(), BUDGET_ARGS);
          await first.openRoot(ORG, 'shared', {
            usdMicros: 5_000,
            tokens: null,
          });
          await second.openChild(ORG, 'shared', 'kid', {
            usdMicros: null,
            tokens: null,
          });
          const outcomes = await Promise.all(
            Array.from({ length: 30 }, (_, index) =>
              (index % 2 === 0 ? first : second)
                .reserve(
                  reserve(
                    index % 3 === 0 ? 'shared' : 'kid',
                    `k-${String(index)}`,
                    400,
                  ),
                )
                .then((r) => r.reservation.granted.usdMicros)
                .catch(() => 0),
            ),
          );
          expect(outcomes.reduce((sum, value) => sum + value, 0)).to.equal(
            4_800,
          );
          expect(
            (await first.remaining(ORG, 'shared')).reserved.usdMicros,
          ).to.equal(4_800);
        });

        it('NL-3: a reservation expires on database time; a late commit still charges', async () => {
          let now = Date.UTC(2026, 9, 3);
          const ledger = scopedStore(
            new PgBudgetLedger(fixedPgTime(() => now)),
            BUDGET_ARGS,
          );
          await ledger.openRoot(ORG, 'exp', { usdMicros: 1_000, tokens: null });
          await ledger.reserve(reserve('exp', 'crashed', 1_000, 0));
          now += 59_000;
          expect(
            (await ledger.remaining(ORG, 'exp')).available.usdMicros,
          ).to.equal(0);
          now += 2_000;
          expect(
            (await ledger.remaining(ORG, 'exp')).available.usdMicros,
          ).to.equal(1_000);
          const state = await stack.admin((c) =>
            c.query(
              `SELECT state FROM harness.budget_reservations WHERE org_id = $1 AND key = 'crashed'`,
              [ORG],
            ),
          );
          expect(state.rows[0].state).to.equal('expired');
          const late = await ledger.commit(ORG, 'exp', 'crashed', {
            usdMicros: 300,
            tokens: 0,
          });
          expect(late.charged.usdMicros).to.equal(300);
          expect(late.remaining.available.usdMicros).to.equal(700);
        });

        it('NL-4: the hold outlives the store instance; settling again answers the first settlement', async () => {
          const before = scopedStore(new PgBudgetLedger(), BUDGET_ARGS);
          await before.openRoot(ORG, 'restart', {
            usdMicros: 1_000,
            tokens: null,
          });
          const first = await before.reserve(
            reserve('restart', 'in-flight', 600, 0),
          );
          const after = scopedStore(new PgBudgetLedger(), BUDGET_ARGS);
          const again = await after.reserve(
            reserve('restart', 'in-flight', 900, 0),
          );
          expect(again.reservation).to.deep.equal(first.reservation);
          expect(
            await budgetCode(after.reserve(reserve('restart', 'next', 500))),
          ).to.equal('BUDGET_INSUFFICIENT');
          const settled = await Promise.all(
            [1, 2, 3].map(() =>
              after.commit(ORG, 'restart', 'in-flight', {
                usdMicros: 100,
                tokens: 0,
              }),
            ),
          );
          expect(settled.map((s) => s.charged.usdMicros)).to.deep.equal([
            100, 100, 100,
          ]);
          expect(
            (await after.remaining(ORG, 'restart')).committed.usdMicros,
          ).to.equal(100);
          expect(
            await budgetCode(after.release(ORG, 'restart', 'in-flight')),
          ).to.equal('OK');
          expect(
            (await after.remaining(ORG, 'restart')).committed.usdMicros,
          ).to.equal(100);
        });

        it("a child admitted in a unit that aborts leaves no account, and its parent's budget is untouched", async () => {
          const ledger = new PgBudgetLedger();
          const scoped = scopedStore(ledger, BUDGET_ARGS);
          await scoped.openRoot(ORG, 'root', {
            usdMicros: 10_000,
            tokens: null,
          });
          const failed = await transactAs(async (tx) => {
            await ledger.admitChildIn(
              tx,
              ORG,
              'child',
              {
                chain: [
                  {
                    runId: 'root',
                    parentRunId: null,
                    cap: { usdMicros: 10_000, tokens: null },
                  },
                ],
                cap: { usdMicros: 3_000, tokens: null },
              },
              Date.now(),
            );
            throw new Error('abort');
          }, ORG).catch((error: Error) => error.message);
          expect(failed).to.equal('abort');
          expect(await scoped.account(ORG, 'child')).to.equal(null);
          await transactAs(
            (tx) =>
              ledger.admitChildIn(
                tx,
                ORG,
                'child',
                {
                  chain: [
                    {
                      runId: 'root',
                      parentRunId: null,
                      cap: { usdMicros: 10_000, tokens: null },
                    },
                  ],
                  cap: { usdMicros: 3_000, tokens: null },
                },
                Date.now(),
              ),
            ORG,
          );
          expect(
            (await scoped.remaining(ORG, 'child')).available.usdMicros,
          ).to.equal(3_000);
          expect(
            (await scoped.remaining(ORG, 'root')).available.usdMicros,
          ).to.equal(10_000);
        });

        it('another org reads no account of this one, and its unit sees no ledger row', async () => {
          const scoped = scopedStore(new PgBudgetLedger(), BUDGET_ARGS);
          await scoped.openRoot(ORG, 'mine', {
            usdMicros: 1_000,
            tokens: null,
          });
          await scoped.reserve(reserve('mine', 'k', 100));
          const other = hexOrg();
          expect(await scoped.account(other, 'mine')).to.equal(null);
          expect(await budgetCode(scoped.remaining(other, 'mine'))).to.equal(
            'BUDGET_ACCOUNT_MISSING',
          );
          const seen = await transactAs(async () => {
            const k = activeUnit()!;
            const n = async (table: string) =>
              (
                await k.query<{ n: number }>(
                  `/* pipeshub:harness.test_count */ SELECT count(*)::int AS n FROM harness.${table}`,
                )
              ).rows[0]!.n;
            return [await n('budget_accounts'), await n('budget_reservations')];
          }, other);
          expect(seen).to.deep.equal([0, 0]);
          const refused = await inOrg(other, () =>
            new PgBudgetLedger().account(ORG, 'mine').catch((e: Error) => e),
          );
          expect(refused).to.be.instanceOf(Error);
        });
      });

    },
  );
}
