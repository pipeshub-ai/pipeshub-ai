/**
 * H4 AR-9 on a MongoDB replica set: the agent erasure queue contract on
 * `agentErasures`, and one retention sweep that retries a failed agent
 * erasure against `MongoMemoryStore`. Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import { AgentErasureModel } from '../../../src/libs/repositories/agent-harness/agent-erasures/schema/agent-erasures.schema';
import { MongoAgentErasureQueue } from '../../../src/libs/repositories/agent-harness/agent-erasures/mongo';
import { AgentMemoryModel } from '../../../src/libs/repositories/agent-harness/memory/schema/memory.schema';
import { MongoMemoryStore } from '../../../src/libs/repositories/agent-harness/memory/mongo';
import { mongoServerTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import { describeAgentErasureQueueContract } from './support/agent-erasure-queue-contract';
import { MEMORY_ORG, memoryFixture } from './support/memory-store-contract';
import { sweeperWithAgentErasures } from './support/agent-erasure-sweeper';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
} from './support/harness-mongo';

registerHarnessModels([AgentErasureModel, AgentMemoryModel] as unknown as Array<Model<never>>);

describe('agent erasure retry on a MongoDB replica set (H4)', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  const fresh = async () => {
    await AgentErasureModel.deleteMany({});
    return { queue: new MongoAgentErasureQueue(), now: () => mongoServerTime.read() };
  };

  describeAgentErasureQueueContract('mongo replica set', fresh);

  it("AE-14: an entry H4 wrote (no `confirmed` field) reads as confirmed and an intent leaves it", async () => {
    const { queue, now } = await fresh();
    const t = await now();
    await AgentErasureModel.collection.insertOne({
      _id: 'org-1:agent-h4',
      orgId: 'org-1',
      agentKey: 'agent-h4',
      failures: 1,
      requestedAt: t,
      nextAttemptAt: t,
      lastErrorType: 'Error',
    } as never);
    await queue.intend('org-1', 'agent-h4', 600_000);
    const [entry] = await queue.due(await now(), 10);
    expect(entry).to.include({ confirmed: true, failures: 1, requestedAt: t, nextAttemptAt: t });
  });

  it("AR-9: a sweep erases the agent's memories through Mongo and settles the entry; another agent's kept", async () => {
    const { queue } = await fresh();
    await AgentMemoryModel.deleteMany({});
    const memories = new MongoMemoryStore();
    await memories.propose(memoryFixture({ scope: 'agent', agentKey: 'agent-x' }));
    await memories.propose(memoryFixture({ scope: 'agent', agentKey: 'agent-y' }));
    await queue.enqueue(MEMORY_ORG, 'agent-x', 'MongoNetworkError');
    const { sweeper } = sweeperWithAgentErasures({ queue, memories, now: await mongoServerTime.read() + 1_000 });
    expect((await sweeper.tick())?.agentErasures).to.equal(1);
    const left = (await memories.list(MEMORY_ORG, { limit: 50 })).map((m) => m.agentKey);
    expect(left).to.deep.equal(['agent-y']);
    expect(await AgentErasureModel.countDocuments({})).to.equal(0);
  });
});
