/**
 * MongoOutboxRepository and MongoMailLogRepository against a real MongoDB
 * (standalone and replica set).
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoUnitOfWork } from '../../src/libs/repositories/mongo';
import { MongoOutboxRepository } from '../../src/libs/repositories/outbox/mongo';
import { MongoMailLogRepository } from '../../src/libs/repositories/mail-log/mongo';
import { OutboxEvent } from '../../src/libs/repositories/outbox/schema/outbox.schema';
import { MailModel } from '../../src/modules/mail/schema/mailInfo.schema';
import type { OutboxEventRow } from '../../src/libs/repositories/outbox';
import { toPlain } from '../../src/libs/repositories/plain';
import { describeOutboxContract } from './contract/outbox.contract';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}

describe('MongoOutboxRepository and MongoMailLogRepository against a real MongoDB', function () {
  this.timeout(60000);
  const uri = requireEnv('MONGO_IT_URI');
  const rsFlag = requireEnv('REPLICA_SET_AVAILABLE');
  if (rsFlag !== 'true' && rsFlag !== 'false') {
    throw new Error(`REPLICA_SET_AVAILABLE must be true or false, got '${rsFlag}'`);
  }
  const dbName = `pipeshub_it_${randomUUID().slice(0, 8)}`;
  let mongo: MongoService;

  before(async () => {
    mongo = new MongoService({ uri, db: dbName });
    await mongo.initialize();
    await OutboxEvent.createCollection();
    await OutboxEvent.syncIndexes();
    await MailModel.createCollection();
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  const oid = (id: string) => new mongoose.Types.ObjectId(id);
  describeOutboxContract({
    repo: new MongoOutboxRepository(),
    mailLog: new MongoMailLogRepository(),
    uow: new MongoUnitOfWork(rsFlag === 'true'),
    transactional: rsFlag === 'true',
    allRows: async () =>
      (await OutboxEvent.find().sort({ createdAt: 1, _id: 1 }).lean()).map((r) =>
        toPlain<OutboxEventRow>(r),
      ),
    clear: async () => {
      await OutboxEvent.deleteMany({});
    },
    setCreatedAt: async (id, at) => {
      await OutboxEvent.collection.updateOne({ _id: oid(id) }, { $set: { createdAt: at } });
    },
    setRow: async (id, set) => {
      await OutboxEvent.collection.updateOne({ _id: oid(id) }, { $set: set });
    },
    mailLogRows: async () => (await MailModel.find().lean()) as unknown as Record<string, unknown>[],
    newId: () => String(new mongoose.Types.ObjectId()),
  });
});
