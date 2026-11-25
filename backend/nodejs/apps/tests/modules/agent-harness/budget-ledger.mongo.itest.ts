/**
 * P6.5 on a real MongoDB replica set (run instructions in
 * execution-store.mongo.itest.ts): the ledger store contract with the shared
 * table (NL-1), parallel reservations across a tree (NL-2), expiry on DB time
 * (NL-3) and a restart of Node mid-call (NL-4).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
import {
  AgentBudgetAccountModel,
  AgentBudgetReservationModel,
} from '../../../src/libs/repositories/agent-harness/budget-ledger/schema/budget-ledger.schema';
import { MongoBudgetLedger } from '../../../src/libs/repositories/agent-harness/budget-ledger/mongo';
import { fixedMongoTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  harnessMongoUri,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import {
  budgetCode,
  describeBudgetLedgerContract,
  ORG,
} from './support/budget-ledger-contract';

registerHarnessModels([
  AgentBudgetAccountModel,
  AgentBudgetReservationModel,
] as unknown as Array<Model<never>>);

describe('P6.5 budget ledger on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeBudgetLedgerContract('mongo', async () => {
    let now = Date.UTC(2026, 9, 3);
    return {
      ledger: new MongoBudgetLedger(fixedMongoTime(() => now)),
      advance: (seconds: number) => {
        now += seconds * 1000;
      },
    };
  });

  it('NL-2b: two store instances (two Node replicas) share one cap', async () => {
    const first = new MongoBudgetLedger();
    const second = new MongoBudgetLedger();
    await first.openRoot(ORG, 'shared', { usdMicros: 5_000, tokens: null });
    await second.openChild(ORG, 'shared', 'kid', { usdMicros: null, tokens: null });
    const outcomes = await Promise.all(
      Array.from({ length: 30 }, (_, index) =>
        (index % 2 === 0 ? first : second)
          .reserve({
            orgId: ORG,
            runId: index % 3 === 0 ? 'shared' : 'kid',
            key: `k-${String(index)}`,
            requested: { usdMicros: 400, tokens: 0 },
            minimum: { usdMicros: 400, tokens: 0 },
            ttlMs: 60_000,
          })
          .then((r) => r.reservation.granted.usdMicros)
          .catch(() => 0),
      ),
    );
    const total = outcomes.reduce((sum, value) => sum + value, 0);
    expect(total).to.equal(4_800);
    expect((await first.remaining(ORG, 'shared')).reserved.usdMicros).to.equal(4_800);
  });

  it('NL-3: a reservation expires on DB time; a late commit still charges', async () => {
    let now = Date.UTC(2026, 9, 3);
    const ledger = new MongoBudgetLedger(fixedMongoTime(() => now));
    await ledger.openRoot(ORG, 'exp', { usdMicros: 1_000, tokens: null });
    await ledger.reserve({
      orgId: ORG,
      runId: 'exp',
      key: 'crashed',
      requested: { usdMicros: 1_000, tokens: 0 },
      minimum: { usdMicros: 0, tokens: 0 },
      ttlMs: 60_000,
    });
    now += 59_000;
    expect((await ledger.remaining(ORG, 'exp')).available.usdMicros).to.equal(0);
    now += 2_000;
    expect((await ledger.remaining(ORG, 'exp')).available.usdMicros).to.equal(1_000);
    expect(
      (await AgentBudgetReservationModel.findById(`${ORG}:exp:crashed`).lean())?.state,
    ).to.equal('expired');
    const late = await ledger.commit(ORG, 'exp', 'crashed', { usdMicros: 300, tokens: 0 });
    expect(late.charged.usdMicros).to.equal(300);
    expect(late.remaining.available.usdMicros).to.equal(700);
  });

  it('NL-4: after a restart the hold is still there until it expires, and settling is idempotent', async () => {
    let now = Date.UTC(2026, 9, 3);
    const before = new MongoBudgetLedger(fixedMongoTime(() => now));
    await before.openRoot(ORG, 'restart', { usdMicros: 1_000, tokens: null });
    await before.reserve({
      orgId: ORG,
      runId: 'restart',
      key: 'in-flight',
      requested: { usdMicros: 600, tokens: 0 },
      minimum: { usdMicros: 0, tokens: 0 },
      ttlMs: 60_000,
    });
    const dbName = mongoose.connection.db!.databaseName;
    await mongoose.disconnect();
    await mongoose.connect(harnessMongoUri(), { dbName });
    const after = new MongoBudgetLedger(fixedMongoTime(() => now));
    expect((await after.remaining(ORG, 'restart')).reserved.usdMicros).to.equal(600);
    expect(
      await budgetCode(
        after.reserve({
          orgId: ORG,
          runId: 'restart',
          key: 'next',
          requested: { usdMicros: 500, tokens: 0 },
          minimum: { usdMicros: 500, tokens: 0 },
          ttlMs: 60_000,
        }),
      ),
    ).to.equal('BUDGET_INSUFFICIENT');
    const settled = await after.commit(ORG, 'restart', 'in-flight', { usdMicros: 100, tokens: 0 });
    const again = await after.commit(ORG, 'restart', 'in-flight', { usdMicros: 100, tokens: 0 });
    expect([settled.charged.usdMicros, again.charged.usdMicros]).to.deep.equal([100, 100]);
    expect((await after.remaining(ORG, 'restart')).committed.usdMicros).to.equal(100);
    now += 120_000;
    expect((await after.remaining(ORG, 'restart')).available.usdMicros).to.equal(900);
  });
});
