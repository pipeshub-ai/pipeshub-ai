/**
 * SEC-AZ-08 (C6.24), DEACT-1, DEACT-3, DEACT-4: a deactivated person's admitted
 * work stops. The real ExecutionService, in-memory execution and schedule
 * stores and the gateway-token service: their runs are cancelled, their
 * schedules paused, a gateway token issued before is refused after, and
 * nobody else's work is touched.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { GatewayTokenAuthority } from '../../../src/modules/agent-harness/application/gateway-token-authority';
import { GatewayTokenService } from '../../../src/modules/agent-harness/application/gateway-token-service';
import {
  ACTOR_DISABLED,
  PrincipalDeactivation,
} from '../../../src/modules/agent-harness/application/principal-deactivation';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  contractClock,
  ContractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import { HARNESS_ENABLED } from './support/rollout';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';
import { aSchedule, TestClock } from './support/schedules';
import { proofFor, testBindingKeys } from './support/binding-proof';

const ADMIN = 'admin-1';

describe('SEC-AZ-08: deactivating a person stops their admitted work', function () {
  this.timeout(20_000);
  let clock: ContractClock;
  let store: InMemoryExecutionStore;
  let h: ContractHarness;
  let schedules: InMemoryScheduleStore;
  let gateway: GatewayTokenService;
  let deactivation: PrincipalDeactivation;

  beforeEach(() => {
    clock = contractClock();
    store = new InMemoryExecutionStore(clock);
    h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads: new InMemoryPayloadStore(),
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    schedules = new InMemoryScheduleStore(new TestClock());
    gateway = new GatewayTokenService({
      tokens: new GatewayTokenAuthority({
        signingKeys: new InMemoryConfigDocumentStore(),
        publishedKeys: new InMemoryConfigDocumentStore(),
        clock,
      }),
      runs: h.service,
      clock,
      bindingKeys: testBindingKeys,
    });
    deactivation = new PrincipalDeactivation({
      runs: store,
      executions: h.service,
      schedules,
    });
  });

  const running = async (overrides: { actorId?: string } = {}): Promise<HarnessRun> => {
    const { run } = await h.admit(overrides);
    const claimed = await h.service.claim({
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: `attempt-${run.runId}`,
      leaseDurationMs: 60_000,
      workerCapabilities: CURRENT_WORKER_CAPABILITIES,
    } as never);
    return claimed.run;
  };

  const statusOf = async (run: HarnessRun) => (await h.service.get(run.orgId, run.runId))!.status;

  it("DEACT-1: cancels every run of theirs that has not ended, queued or running; another person's run goes on", async () => {
    const { orgId, actorId } = h.tenant;
    const queued = (await h.admit()).run;
    const live = await running();
    const other = await running({ actorId: 'someone-else' });

    const counts = await deactivation.deactivate(orgId, actorId, ADMIN);

    expect(counts.cancelledRuns).to.equal(2);
    expect(await statusOf(queued)).to.equal('cancelled');
    expect(await statusOf(live)).to.be.oneOf(['cancelling', 'cancelled']);
    expect(await statusOf(other)).to.equal('running');
    expect(await store.activeRunsOfActor(orgId, actorId)).to.satisfy((runs: HarnessRun[]) =>
      runs.every((run) => run.status === 'cancelling'),
    );
  });

  it('DEACT-1: a gateway token issued for their running run before is refused after (RUN_NOT_LEASED)', async () => {
    const { orgId, actorId } = h.tenant;
    const live = await running();
    const issued = await gateway.forAttempt(orgId, live.runId, live.attemptId!, proofFor(orgId, live.runId, live.attemptId!));
    expect((await gateway.modelGrant(issued.token, live.runId)).actorId).to.equal(actorId);

    await deactivation.deactivate(orgId, actorId, ADMIN);

    const refused = await gateway.modelGrant(issued.token, live.runId).catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('RUN_NOT_LEASED');
    const minted = await gateway.forAttempt(orgId, live.runId, live.attemptId!, proofFor(orgId, live.runId, live.attemptId!)).catch((error: unknown) => error);
    expect((minted as HarnessConflictError).code).to.equal('RUN_NOT_LEASED');
  });

  it('DEACT-1: pauses their schedules and triggers with the reason a refused firing gives', async () => {
    const { orgId, actorId } = h.tenant;
    const mine = await schedules.create(aSchedule({ orgId, actorId }), { perActor: 10, perOrg: 10 });
    const theirs = await schedules.create(aSchedule({ orgId, actorId: 'someone-else' }), { perActor: 10, perOrg: 10 });

    const counts = await deactivation.deactivate(orgId, actorId, ADMIN);

    expect(counts.pausedSchedules).to.equal(1);
    expect(await schedules.get(orgId, mine.id)).to.deep.include({ status: 'paused', statusReason: ACTOR_DISABLED, nextFireAt: null });
    expect((await schedules.get(orgId, theirs.id))?.status).to.equal('active');
  });

  it('DEACT-3: calling it again finishes what is left and changes nothing already done', async () => {
    const { orgId, actorId } = h.tenant;
    const queued = (await h.admit()).run;
    await deactivation.deactivate(orgId, actorId, ADMIN);
    const before = await h.service.get(orgId, queued.runId);
    const again = await deactivation.deactivate(orgId, actorId, ADMIN);
    expect(again).to.deep.equal({ cancelledRuns: 0, pausedSchedules: 0 });
    expect(await h.service.get(orgId, queued.runId)).to.deep.equal(before);
  });

  it('DEACT-4: a run that moved between the read and the cancel is read again and cancelled', async () => {
    const { orgId, actorId } = h.tenant;
    const queued = (await h.admit()).run;
    let stale = true;
    const moving = new PrincipalDeactivation({
      runs: {
        activeRunsOfActor: async (o, a) => {
          const runs = await store.activeRunsOfActor(o, a);
          if (!stale) return runs;
          stale = false;
          return runs.map((run) => ({ ...run, revision: run.revision - 1 }));
        },
      },
      executions: h.service,
      schedules,
    });
    expect((await moving.deactivate(orgId, actorId, ADMIN)).cancelledRuns).to.equal(1);
    expect(await statusOf(queued)).to.equal('cancelled');
  });

  it('DEACT-4: runs that keep moving fail the call, so the route answers a retry', async () => {
    const { orgId, actorId } = h.tenant;
    await h.admit();
    const moving = new PrincipalDeactivation({
      runs: {
        activeRunsOfActor: async (o, a) =>
          (await store.activeRunsOfActor(o, a)).map((run) => ({ ...run, revision: run.revision - 1 })),
      },
      executions: h.service,
      schedules,
    });
    const failed = await moving.deactivate(orgId, actorId, ADMIN).catch((error: unknown) => error);
    expect(failed).to.be.instanceOf(HarnessConflictError);
  });
});
