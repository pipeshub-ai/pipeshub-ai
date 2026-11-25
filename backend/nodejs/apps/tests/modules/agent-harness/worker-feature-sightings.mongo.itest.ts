/**
 * DUR2-W9 on a real MongoDB replica set: the WorkerFeatureSightings contract on
 * `MongoWorkerFeatureSightings`, one document per feature beside the control
 * plane's liveness record, which it leaves alone (run instructions in
 * execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import {
  AgentHarnessLivenessModel,
  MongoControlPlaneLiveness,
} from '../../../src/libs/repositories/agent-harness/control-plane-liveness/mongo';
import {
  featureLivenessId,
  MongoWorkerFeatureSightings,
} from '../../../src/libs/repositories/agent-harness/worker-feature-sightings/mongo';
import { fixedMongoTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import { describeWorkerFeatureSightingsContract } from './support/worker-feature-sightings-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoTimeFor,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { contractClock } from './support/execution-store-contract';

registerHarnessModels([AgentHarnessLivenessModel] as unknown as Array<Model<never>>);

describe('DUR2-W9 worker-feature sightings on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeWorkerFeatureSightingsContract('mongo replica set', async (clock) => {
    await resetHarnessMongo();
    const time = fixedMongoTime(() => clock.now());
    return { replica: () => new MongoWorkerFeatureSightings(time) };
  });

  it('keeps one liveness document per feature, however often it is seen', async () => {
    const sightings = new MongoWorkerFeatureSightings(mongoTimeFor(contractClock()));
    await sightings.saw(['agent_code']);
    await sightings.saw(['agent_code', 'chat']);
    expect(await AgentHarnessLivenessModel.countDocuments({ _id: featureLivenessId('agent_code') })).to.equal(1);
  });

  it('leaves the control plane liveness record as it was', async () => {
    const clock = contractClock();
    const time = mongoTimeFor(clock);
    const liveness = new MongoControlPlaneLiveness(time);
    const first = await liveness.observe();
    await new MongoWorkerFeatureSightings(time).saw(['agent_code']);
    clock.set(2_000);
    const second = await liveness.observe();
    expect(second.previousSeenAt).to.equal(first.now);
  });
});
