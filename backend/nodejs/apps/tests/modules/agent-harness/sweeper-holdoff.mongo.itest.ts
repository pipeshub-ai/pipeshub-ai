/**
 * O-6a / C-13 on a real MongoDB replica set: after a control-plane gap the
 * returning sweeper expires what lapsed on its first tick (the gap is logged,
 * not waited out), while an explicit holdoff in the database still holds
 * across a leader change (run instructions in execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import {
  AgentHarnessLivenessModel,
  MongoControlPlaneLiveness,
} from '../../../src/libs/repositories/agent-harness/control-plane-liveness/mongo';
import { MongoLeaderLease } from '../../../src/libs/repositories/agent-harness/leader-lease/mongo';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  RunSweeper,
  SWEEP_INTERVAL_MS,
  SWEEPER_LEASE_MS,
} from '../../../src/modules/agent-harness/application/run-sweeper';
import { MAX_LEASE_DURATION_MS } from '../../../src/modules/agent-harness/domain/authority';
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

const messages: string[] = [];
const logger: HarnessLogger = {
  debug: () => undefined,
  info: (message) => messages.push(message),
  warn: (message) => messages.push(message),
  error: (message) => messages.push(message),
};

describe('O-6a / C-13 sweeper after a control-plane gap on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  const fleet = async () => {
    const clock = contractClock();
    const time = mongoTimeFor(clock);
    const h = createContractHarness(mongoBackend({ clock }), clock);
    const runs = [];
    for (let index = 0; index < 3; index += 1) {
      const { run } = await h.admit();
      runs.push(await h.claim(run, `worker-${String(index)}`, 1_000));
    }
    const sweeper = (holder: string) =>
      new RunSweeper({
        executions: h.service,
        lease: new MongoLeaderLease(time),
        liveness: new MongoControlPlaneLiveness(time),
        logger,
        holderId: holder,
      });
    return { clock, h, runs, sweeper };
  };

  it('the replica that returns after Node was down expires the lapsed leases on its first tick', async () => {
    const { clock, h, runs, sweeper } = await fleet();
    expect(await sweeper('replica-a').tick()).to.equal(0);
    // Every replica is gone for 10 minutes; B takes the lapsed sweeper lease.
    clock.set(clock.now() + 10 * 60_000);
    messages.length = 0;
    expect(await sweeper('replica-b').tick()).to.equal(3);
    expect(messages).to.include('sweeper.gap');
    for (const run of runs) {
      expect((await h.service.get(run.orgId, run.runId))?.status).to.not.equal('running');
    }
  });

  it('an explicit holdoff in the database holds across a leader change, then expires', async () => {
    const { clock, h, runs, sweeper } = await fleet();
    expect(await sweeper('replica-a').tick()).to.equal(0);
    const until = clock.now() + MAX_LEASE_DURATION_MS;
    await new MongoControlPlaneLiveness(mongoTimeFor(clock)).holdOff(until);
    clock.set(clock.now() + SWEEPER_LEASE_MS);
    const b = sweeper('replica-b');
    let swept = 0;
    while (clock.now() < until) {
      swept += await b.tick();
      clock.set(clock.now() + SWEEP_INTERVAL_MS);
    }
    expect(swept).to.equal(0);
    expect(await b.tick()).to.equal(3);
    for (const run of runs) {
      expect((await h.service.get(run.orgId, run.runId))?.status).to.not.equal('running');
    }
  });
});
