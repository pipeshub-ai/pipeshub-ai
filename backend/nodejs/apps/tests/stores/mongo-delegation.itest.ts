/**
 * Mongo delegation repository against a real MongoDB.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoDelegationRepository } from '../../src/libs/repositories/delegation/mongo';
import { ServiceAccountDelegation } from '../../src/modules/oauth_provider/schema/service-account-delegation.schema';
import { DelegationEvent } from '../../src/modules/oauth_provider/schema/delegation-event.schema';
import { describeDelegationContract } from './contract/delegation.contract';

describe('Delegation repository against a real MongoDB', function () {
  this.timeout(60000);
  const uri = process.env.MONGO_IT_URI;
  if (!uri) throw new Error('MONGO_IT_URI must be set for the MongoDB tests');
  let mongo: MongoService;

  before(async () => {
    mongo = new MongoService({ uri, db: `pipeshub_it_${randomUUID().slice(0, 8)}` });
    await mongo.initialize();
    await ServiceAccountDelegation.createCollection();
    await ServiceAccountDelegation.syncIndexes();
    await DelegationEvent.createCollection();
  });
  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  describeDelegationContract({
    repo: new MongoDelegationRepository(),
    newId: () => String(new mongoose.Types.ObjectId()),
    setEventCreatedAt: async (id, at) => {
      await DelegationEvent.collection.updateOne({ _id: new mongoose.Types.ObjectId(id) }, { $set: { createdAt: at } });
    },
  });
});
