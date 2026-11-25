/**
 * The ErasureJobStore contract on `MongoErasureJobStore` over a replica set,
 * its DB time played by the suite's clock (run as execution-store.mongo.itest.ts says).
 */
import 'reflect-metadata';
import { Model } from 'mongoose';
import { fixedMongoTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import { MongoErasureJobStore } from '../../../src/libs/repositories/agent-harness/session-erasure/mongo';
import { AgentSessionErasureModel } from '../../../src/libs/repositories/agent-harness/session-erasure/schema/session-erasure.schema';
import { describeErasureJobStoreContract } from './support/erasure-job-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';

registerHarnessModels([AgentSessionErasureModel] as unknown as Array<Model<never>>);

describe('ErasureJobStore on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describeErasureJobStoreContract('mongo replica set', async (clock) => {
    await resetHarnessMongo();
    return new MongoErasureJobStore(fixedMongoTime(() => clock.now()));
  });
});
