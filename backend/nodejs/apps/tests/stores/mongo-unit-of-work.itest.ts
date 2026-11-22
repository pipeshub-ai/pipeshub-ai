/**
 * MongoUnitOfWork against a real MongoDB, as a replica set and as a single server.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoUnitOfWork, sessionOf } from '../../src/libs/repositories/mongo';
import { describeUnitOfWorkContract } from './contract/unit-of-work.contract';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}

describe('MongoUnitOfWork against a real MongoDB', function () {
  this.timeout(60000);
  const uri = requireEnv('MONGO_IT_URI');
  const rsFlag = requireEnv('REPLICA_SET_AVAILABLE');
  if (rsFlag !== 'true' && rsFlag !== 'false') {
    throw new Error(`REPLICA_SET_AVAILABLE must be true or false, got '${rsFlag}'`);
  }
  const rsAvailable = rsFlag === 'true';
  const dbName = `pipeshub_it_${randomUUID().slice(0, 8)}`;
  let mongo: MongoService;
  const rows = () => mongoose.connection.collection('uow_contract');

  before(async () => {
    mongo = new MongoService({ uri, db: dbName });
    await mongo.initialize();
    // Collections must exist before a transaction touches them (Mongo < 4.4 rule, harmless later).
    await mongoose.connection.db!.createCollection('uow_contract');
    await rows().createIndex({ key: 1 }, { unique: true });
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  describeUnitOfWorkContract({
    uow: new MongoUnitOfWork(rsAvailable),
    transactional: rsAvailable,
    put: async (tx, key) => {
      await rows().insertOne({ key }, { session: sessionOf(tx) });
    },
    has: async (key) => (await rows().countDocuments({ key })) === 1,
  });
});
