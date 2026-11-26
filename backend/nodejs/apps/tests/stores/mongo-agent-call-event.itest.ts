/**
 * Mongo agent call repository against a real MongoDB.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoAgentCallRepository } from '../../src/libs/repositories/agent-call-event/mongo';
import { AgentCallEvent } from '../../src/modules/oauth_provider/schema/agent-call-event.schema';
import { describeAgentCallContract } from './contract/agent-call-event.contract';

describe('Agent call repository against a real MongoDB', function () {
  this.timeout(60000);
  const uri = process.env.MONGO_IT_URI;
  if (!uri) throw new Error('MONGO_IT_URI must be set for the MongoDB tests');
  let mongo: MongoService;

  before(async () => {
    mongo = new MongoService({ uri, db: `pipeshub_it_${randomUUID().slice(0, 8)}` });
    await mongo.initialize();
    await AgentCallEvent.createCollection();
    await AgentCallEvent.syncIndexes();
  });
  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  describeAgentCallContract({
    repo: new MongoAgentCallRepository(),
    newId: () => String(new mongoose.Types.ObjectId()),
  });

  it('expires rows after 30 days with a TTL index on createdAt', async () => {
    const ttl = (await AgentCallEvent.collection.indexes()).find((i) => i.key.createdAt === 1 && Object.keys(i.key).length === 1);
    expect(ttl?.expireAfterSeconds).to.equal(30 * 24 * 60 * 60);
  });
});
