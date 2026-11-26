/**
 * CLEANUP-G6: refused sandbox reaps kept in `agentSandboxReaps` on a MongoDB
 * replica set. Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { Model } from 'mongoose';
import { MongoPendingReaps } from '../../../src/libs/repositories/agent-harness/pending-reaps/mongo';
import { AgentSandboxReapModel } from '../../../src/libs/repositories/agent-harness/pending-reaps/schema/pending-reaps.schema';
import { connectHarnessMongo, disconnectHarnessMongo, registerHarnessModels, resetHarnessMongo } from './support/harness-mongo';
import { describePendingReapsContract } from './support/pending-reaps-contract';

registerHarnessModels([AgentSandboxReapModel] as unknown as Array<Model<never>>);

describe('agentSandboxReaps on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  describePendingReapsContract('Mongo', async () => {
    await resetHarnessMongo();
    return new MongoPendingReaps();
  });
});
