/**
 * UX-07: the agent card's "How it runs" settings are each checked by the server when they are saved. A schedule's
 * budget per run may not be more than the ceiling an extension point holds its runs to, so a schedule is never made
 * that every firing's reservation would refuse.
 */
import { expect } from 'chai';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import {
  CreateScheduleRequest,
  ScheduleCaller,
  ScheduleService,
} from '../../../src/modules/agent-harness/application/schedule-service';
import { HarnessScheduleRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import type { RunBudgetCeiling } from '../../../src/modules/agent-harness/ports/cost-ledger';
import { ALICE, BOB, FakeActors, FakeAdmission, FakeSetting, ORG, TestClock } from './support/schedules';
import { testControlPlane } from '../control-plane/support/in-memory';
import { without } from './support/without';

const alice: ScheduleCaller = { orgId: ORG, userId: ALICE, via: 'session' };
const bob: ScheduleCaller = { orgId: ORG, userId: BOB, via: 'session' };

const CRON: CreateScheduleRequest = {
  name: 'Weekly support digest',
  agentKey: 'support-agent',
  input: { query: 'Summarise new support tickets' },
  trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin' },
};

const micros = (usd: number) => Math.round(usd * 1_000_000);

type Ceiling = Awaited<ReturnType<RunBudgetCeiling['ceiling']>>;

class FakeCeiling implements RunBudgetCeiling {
  ceilingOf: Ceiling = null;
  failing = false;
  asked: Array<{ orgId: string; agentKey: string | null }> = [];

  async ceiling(orgId: string, agentKey: string | null): Promise<Ceiling> {
    this.asked.push({ orgId, agentKey });
    if (this.failing) throw new Error('policy store down');
    return this.ceilingOf === null ? null : { ...this.ceilingOf };
  }
}

async function refusal(promise: Promise<unknown>): Promise<HarnessScheduleRefusedError> {
  try {
    await promise;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessScheduleRefusedError);
    return error as HarnessScheduleRefusedError;
  }
  throw new Error('expected a refusal');
}

describe('UX-07: runtime settings are validated on save (schedule budget within its ceiling)', () => {
  let store: InMemoryScheduleStore;
  let budgets: FakeCeiling;
  let service: ScheduleService;

  beforeEach(() => {
    const clock = new TestClock(Date.parse('2026-07-01T00:00:00.000Z'));
    store = new InMemoryScheduleStore(clock);
    budgets = new FakeCeiling();
    const actors = new FakeActors();
    const setting = new FakeSetting();
    service = new ScheduleService({
      transaction: testControlPlane(),
      store,
      firer: new ScheduleFirer({ store, admission: new FakeAdmission(), actors, setting }),
      actors,
      setting,
      budgets,
      agentVersions: {
        resolve: async (_orgId, actorId, agentKey) => (actorId === ALICE && agentKey === 'support-agent' ? 1 : null),
      },
    });
  });

  it('UX-07: a budget per run above the ceiling is refused naming what bounds it; nothing is stored', async () => {
    budgets.ceilingOf = { usdMicros: micros(2), why: 'the bound named here' };
    const refused = await refusal(service.create(alice, { ...CRON, maxCostUsd: 3 }));
    expect(refused.code).to.equal('SCHEDULE_BUDGET_EXCEEDS_CEILING');
    expect(refused.field).to.equal('maxCostUsd');
    expect(refused.message).to.equal("A run's budget can be at most USD 2, the bound named here");
    expect(await store.list(ORG, {}, 10)).to.deep.equal([]);
    expect(budgets.asked).to.deep.equal([{ orgId: ORG, agentKey: 'support-agent' }]);
    expect((await service.create(alice, { ...CRON, maxCostUsd: 2 })).schedule.maxCostUsd).to.equal(2);
  });

  it('UX-07: an assistant schedule asks for the ceiling with no agent', async () => {
    budgets.ceilingOf = { usdMicros: micros(1), why: 'x' };
    const assistant = without(CRON, 'agentKey');
    expect((await refusal(service.create(alice, { ...assistant, maxCostUsd: 2 }))).code).to.equal(
      'SCHEDULE_BUDGET_EXCEEDS_CEILING',
    );
    expect(budgets.asked.at(-1)).to.deep.equal({ orgId: ORG, agentKey: null });
  });

  it("UX-07: someone who may not run the agent is told it is not found, never the ceiling", async () => {
    budgets.ceilingOf = { usdMicros: micros(1), why: 'x' };
    const refused = await refusal(service.create(bob, { ...CRON, maxCostUsd: 3 }));
    expect(refused.message).to.not.include('USD 1');
    expect(budgets.asked).to.deep.equal([]);
  });

  it('UX-07: no ceiling, or no budget per run, leaves only the release ceiling', async () => {
    expect((await service.create(alice, { ...CRON, maxCostUsd: 5 })).schedule.maxCostUsd).to.equal(5);
    budgets.ceilingOf = { usdMicros: micros(1), why: 'x' };
    expect((await service.create(alice, CRON)).schedule.maxCostUsd).to.equal(null);
    expect((await refusal(service.create(alice, { ...CRON, maxCostUsd: 6 }))).message).to.include('at most 5 USD');
  });

  it('UX-07: a ceiling that cannot be read does not block the save; the ledger still holds every run at its reservation', async () => {
    budgets.failing = true;
    expect((await service.create(alice, { ...CRON, maxCostUsd: 3 })).schedule.status).to.equal('active');
  });
});
