/**
 * P8a BG: a fired run pins `runKind: scheduled`, `via: trigger` and the
 * firing's budget; the budget ledger (P6.5) opened from that pin refuses a
 * reservation above it.
 */
import { expect } from 'chai';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { pinnedCap } from '../../../src/modules/agent-harness/domain/budget-ledger';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { resolveRunSpec, RUN_SPEC_BUDGETS } from '../../../src/modules/agent-harness/domain/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { goldenSpecSubject } from './support/golden-run-spec';
import { scheduledWorld, ScheduledWorld } from './support/scheduled-admission';
import { aSchedule, ALICE, ORG } from './support/schedules';

const LIMITS = { perActor: 50, perOrg: 500 };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

async function fire(w: ScheduledWorld, maxCostUsd?: number) {
  const schedule = await w.schedules.create(
    aSchedule({ agentKey: 'agent-1', ...(maxCostUsd !== undefined ? { maxCostUsd } : {}) }, w.clock.value),
    LIMITS,
  );
  w.clock.value = (schedule.nextFireAt as number) + 1_000;
  await w.firer.tick();
  const [firing] = await w.schedules.firings(ORG, schedule.id, 1);
  expect(firing?.status, firing?.reason ?? '').to.equal('admitted');
  const run = await w.executions.get(ORG, firing!.runId!);
  const claimed = await w.service.claim({
    orgId: ORG,
    runId: run!.runId,
    workerId: 'worker-1',
    attemptId: 'attempt-1',
    leaseDurationMs: 60_000,
  });
  return { schedule, firing: firing!, run: run!, claimed };
}

describe('P8a budgets per firing (BG)', () => {
  it('BG-1: the fired run pins runKind scheduled, via trigger and the firing budget; run.admitted says scheduled', async () => {
    const w = scheduledWorld();
    const { firing, run, claimed } = await fire(w, 0.5);
    expect(run.actorId).to.equal(ALICE);
    const spec = claimed.pinned!.spec;
    expect(spec.runKind).to.equal('scheduled');
    expect(spec.principalRef).to.equal(`p_${ALICE}`);
    expect(spec.budgets.maxCostUsd).to.equal(0.5);
    expect(spec.extensions?.['x.pipeshub']).to.deep.include({ via: 'trigger' });
    const subject = await w.executions.readClaimSubject(ORG, run.runId);
    expect(subject?.trigger).to.deep.equal({
      scheduleId: firing.scheduleId,
      firingId: firing.id,
      slotKey: firing.slotKey,
      maxCostUsd: 0.5,
    });
    const admitted = (await w.executions.journal.readStream(ORG, run.sessionId, 0, 10)).find(
      (e) => e.type === 'run.admitted',
    );
    expect(admitted?.data).to.deep.include({ runKind: 'scheduled', via: 'trigger' });
  });

  it('BG-1: without a firing budget the run pins the ceiling; a person\'s run stays interactive', async () => {
    const w = scheduledWorld();
    const { claimed } = await fire(w);
    expect(claimed.pinned!.spec.budgets.maxCostUsd).to.equal(RUN_SPEC_BUDGETS.maxCostUsd);
    const person = resolveRunSpec(goldenSpecSubject(), DURABLE);
    expect(person.runKind).to.equal('interactive');
    expect(person.budgets.maxCostUsd).to.equal(RUN_SPEC_BUDGETS.maxCostUsd);
    // A trigger can only lower the ceiling.
    const raised = resolveRunSpec(
      { ...goldenSpecSubject(), trigger: { scheduleId: 's', firingId: 'f', slotKey: 'c:x', maxCostUsd: 50 } },
      DURABLE,
    );
    expect(raised.budgets.maxCostUsd).to.equal(RUN_SPEC_BUDGETS.maxCostUsd);
  });

  it('BG-2: the ledger opened from the pin refuses a reservation above the firing budget', async () => {
    const w = scheduledWorld();
    const { run, claimed } = await fire(w, 0.5);
    const ledger = new InMemoryBudgetLedger(() => w.clock.value);
    await ledger.openRoot(ORG, run.runId, pinnedCap(claimed.pinned!.spec));
    const reserve = (key: string, usd: number) =>
      ledger.reserve({
        orgId: ORG,
        runId: run.runId,
        key,
        requested: { usdMicros: usd * 1_000_000, tokens: 1_000 },
        minimum: { usdMicros: usd * 1_000_000, tokens: 1_000 },
        ttlMs: 60_000,
      });
    const refused = await reserve('too-much', 0.6).catch((e: unknown) => e);
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('BUDGET_INSUFFICIENT');
    const granted = await reserve('call-1', 0.4);
    expect(granted.reservation.granted.usdMicros).to.equal(400_000);
    await ledger.commit(ORG, run.runId, 'call-1', { usdMicros: 500_000, tokens: 1_000 });
    const exhausted = await reserve('call-2', 0.01).catch((e: unknown) => e);
    expect((exhausted as HarnessConflictError).code).to.equal('BUDGET_EXHAUSTED');
  });
});
