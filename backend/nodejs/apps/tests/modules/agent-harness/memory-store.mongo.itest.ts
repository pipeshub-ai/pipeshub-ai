/**
 * P7.3 on a MongoDB replica set: the memory store contract (MS-1..MS-6) on
 * `agentMemories` with its migration's indexes, and governed memory through
 * the real admission store, share predicate and conversation (AD, SV-8, ER).
 * Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { Model } from 'mongoose';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { AgentMemoryModel } from '../../../src/libs/repositories/agent-harness/memory/schema/memory.schema';
import { MongoMemoryStore } from '../../../src/libs/repositories/agent-harness/memory/mongo';
import { describeMemoryAdmissionContract } from './support/memory-admission-contract';
import { describeMemoryStoreContract } from './support/memory-store-contract';
import { mongoCollaboratorBackend } from './support/collaborator-mongo';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
} from './support/harness-mongo';

registerHarnessModels([AgentMemoryModel, ChatSession] as unknown as Array<Model<never>>);

describe('governed memory on a MongoDB replica set (P7.3)', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  const fresh = async (): Promise<MongoMemoryStore> => {
    await AgentMemoryModel.deleteMany({});
    return new MongoMemoryStore();
  };

  describeMemoryStoreContract('mongo replica set', fresh);
  describeMemoryAdmissionContract('mongo replica set', mongoCollaboratorBackend, fresh);
});
