/**
 * OPS1: how long a run waits for a sweeper after the leader went away, on a
 * real MongoDB replica set with the store's clock under the test's control
 * (run instructions in execution-store.mongo.itest.ts). A leader that stops
 * hands over at once; one that dies is covered by its lease.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import {
  AgentHarnessLivenessModel,
  MongoControlPlaneLiveness,
} from '../../../src/libs/repositories/agent-harness/control-plane-liveness/mongo';
import { MongoLeaderLease } from '../../../src/libs/repositories/agent-harness/leader-lease/mongo';
import { silentHarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  RunSweeper,
  SWEEP_INTERVAL_MS,
  SWEEPER_LEASE_MS,
} from '../../../src/modules/agent-harness/application/run-sweeper';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  mongoTimeFor,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import {
  contractClock,
  createContractHarness,
} from './support/execution-store-contract';

registerHarnessModels([AgentHarnessLivenessModel] as unknown as Array<
  Model<never>
>);

const ATTEMPT_LEASE_MS = 1_000;

describe('OPS1 sweeper handover on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  /** One run whose worker went away right after its claim, and replicas that sweep on the store's clock. */
  const orphaned = async () => {
    const clock = contractClock();
    const time = mongoTimeFor(clock);
    const h = createContractHarness(mongoBackend({ clock }), clock);
    // A deadline well past every lease here: the sweep re-queues the run, it does not fail it.
    const { run } = await h.admit({ deadlineMs: 20 * SWEEPER_LEASE_MS });
    const claimed = await h.claim(run, 'worker-gone', ATTEMPT_LEASE_MS);
    const replica = (holder: string) =>
      new RunSweeper({
        executions: h.service,
        lease: new MongoLeaderLease(time),
        liveness: new MongoControlPlaneLiveness(time),
        logger: silentHarnessLogger,
        holderId: holder,
      });
    const status = async () =>
      (await h.service.get(claimed.orgId, claimed.runId))?.status;
    return { clock, h, claimed, replica, status };
  };

  it('OPS1-1-2: a leader that stops hands over at once: the next replica sweeps on its very next tick', async () => {
    const { clock, replica, status } = await orphaned();
    const a = replica('replica-a');
    expect(await a.tick()).to.equal(0);
    clock.set(clock.now() + ATTEMPT_LEASE_MS);
    await a.stop();
    expect(await replica('replica-b').tick()).to.equal(1);
    expect(await status()).to.equal('queued');
  });

  it('OPS1-1-3: a leader that died keeps the others out until its lease lapses, and no longer', async () => {
    const { clock, replica, status } = await orphaned();
    const died = clock.now();
    expect(await replica('replica-a').tick()).to.equal(0);
    const b = replica('replica-b');
    clock.set(died + SWEEPER_LEASE_MS - 1);
    expect(await b.tick()).to.equal(0);
    expect(await status()).to.equal('running');
    clock.set(died + SWEEPER_LEASE_MS);
    expect(await b.tick()).to.equal(1);
    expect(await status()).to.equal('queued');
  });

  for (const phaseMs of [1, SWEEP_INTERVAL_MS / 2, SWEEP_INTERVAL_MS - 1]) {
    it(`OPS1-1-3: ticking every interval from phase ${String(phaseMs)} ms, the survivor has swept by the lease plus one interval`, async () => {
      const { clock, replica, status } = await orphaned();
      const died = clock.now();
      expect(await replica('replica-a').tick()).to.equal(0);
      const b = replica('replica-b');
      // The first tick that sweeps: a run left queued with no worker is later failed as never started.
      let sweptAt: number | null = null;
      for (
        let at = died + phaseMs;
        sweptAt === null && at <= died + 10 * SWEEPER_LEASE_MS;
        at += SWEEP_INTERVAL_MS
      ) {
        clock.set(at);
        if ((await b.tick()) === 1) sweptAt = at;
      }
      expect(await status()).to.equal('queued');
      expect(sweptAt).to.be.within(
        died + SWEEPER_LEASE_MS,
        died + SWEEPER_LEASE_MS + SWEEP_INTERVAL_MS,
      );
    });
  }

  it('OPS1-1-5: two sweepers that both believe they lead expire a run once', async () => {
    const { clock, h, claimed, status } = await orphaned();
    clock.set(clock.now() + ATTEMPT_LEASE_MS);
    const swept = await Promise.all([
      h.service.sweep(10),
      h.service.sweep(10),
      h.service.sweep(10),
    ]);
    expect(swept.reduce((sum, count) => sum + count, 0)).to.equal(1);
    expect(await status()).to.equal('queued');
    const run = await h.service.get(claimed.orgId, claimed.runId);
    expect(run?.fence).to.equal(claimed.fence);
  });
});
