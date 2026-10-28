/**
 * ConversationRepository against a real MongoDB, as a replica set and as a single server.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import { expect } from 'chai';
import mongoose from 'mongoose';
import sinon from 'sinon';
import { ChatSession } from '../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoUnitOfWork } from '../../src/libs/repositories/mongo';
import { MongoConversationRepository } from '../../src/libs/repositories/conversation/mongo';
import { describeConversationContract } from './contract/conversation.contract';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}

describe('MongoConversationRepository against a real MongoDB', function () {
  this.timeout(60000);
  const uri = requireEnv('MONGO_IT_URI');
  const rsFlag = requireEnv('REPLICA_SET_AVAILABLE');
  if (rsFlag !== 'true' && rsFlag !== 'false') throw new Error(`REPLICA_SET_AVAILABLE must be true or false, got '${rsFlag}'`);
  const rsAvailable = rsFlag === 'true';
  const dbName = `pipeshub_it_${randomUUID().slice(0, 8)}`;
  let mongo: MongoService;

  before(async () => {
    mongo = new MongoService({ uri, db: dbName });
    await mongo.initialize();
    // Collections must exist before a transaction touches them.
    for (const model of Object.values(mongoose.models)) await model.createCollection().catch(() => undefined);
    for (const model of Object.values(mongoose.models)) await model.syncIndexes();
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  describeConversationContract({
    repo: new MongoConversationRepository(),
    uow: new MongoUnitOfWork(rsAvailable),
    transactional: rsAvailable,
    newId: () => new mongoose.Types.ObjectId().toHexString(),
  });

  it('clearing the share link removes the stored field', async () => {
    const repo = new MongoConversationRepository();
    const org = new mongoose.Types.ObjectId().toHexString();
    const user = new mongoose.Types.ObjectId().toHexString();
    const s = await repo.openSession(
      { orgId: org, userId: user, initiator: user, lastActivityAt: 1, status: 'Complete', sessionType: 'chat' },
      { messageType: 'user_query', content: 'q' },
    );
    const scope = { kind: 'chat' } as const;
    await ChatSession.updateOne({ _id: s._id }, { $set: { shareLink: 'https://x/chat/1', isShared: true } });

    const cleared = await repo.setSharing(scope, s._id, { isShared: false, sharedWith: [], clearShareLink: true });

    expect(cleared).to.not.have.property('shareLink');
    const stored = await ChatSession.findById(s._id).lean();
    expect(stored).to.not.have.property('shareLink');
    expect(stored!.isShared).to.equal(false);
  });

  it('reads a page of sessions\' messages with one query', async () => {
    const repo = new MongoConversationRepository();
    const org = new mongoose.Types.ObjectId().toHexString();
    const user = new mongoose.Types.ObjectId().toHexString();
    const ids: string[] = [];
    for (let i = 0; i < 5; i++) {
      const s = await repo.openSession(
        { orgId: org, userId: user, initiator: user, lastActivityAt: i, status: 'Complete', sessionType: 'chat' },
        { messageType: 'user_query', content: `q${i}` },
      );
      ids.push(s._id);
    }
    const find = sinon.spy(ChatSessionMessage, 'find');
    try {
      const grouped = await repo.listMessagesOfSessions(ids, { orgId: org, withCitations: true });
      expect(grouped.size).to.equal(5);
      expect(find.callCount).to.equal(1);
    } finally {
      find.restore();
    }
  });
});
