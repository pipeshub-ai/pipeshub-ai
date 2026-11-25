/**
 * The ControlPlaneLiveness contract on `MongoControlPlaneLiveness` on a
 * replica set, DB time played by the suite's clock; each replica is its own
 * adapter instance (run as execution-store.mongo.itest.ts says).
 */
import 'reflect-metadata';
import { Model } from 'mongoose';
import {
  AgentHarnessLivenessModel,
  MongoControlPlaneLiveness,
} from '../../../src/libs/repositories/agent-harness/control-plane-liveness/mongo';
import { fixedMongoTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import { describeControlPlaneLivenessContract } from './support/control-plane-liveness-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';

registerHarnessModels([AgentHarnessLivenessModel] as unknown as Array<Model<never>>);

describe('ControlPlaneLiveness on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describeControlPlaneLivenessContract('mongo replica set', async (clock) => {
    await resetHarnessMongo();
    const time = fixedMongoTime(() => clock.now());
    return { replica: () => new MongoControlPlaneLiveness(time) };
  });
});
