/**
 * MongoDocumentRepository against a real MongoDB (standalone and replica set).
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoDocumentRepository } from '../../src/libs/repositories/document/mongo';
import { DocumentModel } from '../../src/modules/storage/schema/document.schema';
import { describeDocumentContract } from './contract/document.contract';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}

describe('MongoDocumentRepository against a real MongoDB', function () {
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
    await DocumentModel.createCollection();
    await DocumentModel.syncIndexes();
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  describeDocumentContract({
    repo: new MongoDocumentRepository(),
    newId: () => String(new mongoose.Types.ObjectId()),
  });
});
