/**
 * The DataKeyStore contract on `MongoDataKeyStore` over a replica set, with
 * the migrated unique indexes (run as execution-store.mongo.itest.ts says).
 */
import 'reflect-metadata';
import { MongoDataKeyStore } from '../../../src/libs/repositories/agent-harness/data-protection/mongo';
import { describeDataKeyStoreContract } from './support/data-key-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  resetHarnessMongo,
} from './support/harness-mongo';

describe('DataKeyStore on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describeDataKeyStoreContract('mongo replica set', async () => {
    await resetHarnessMongo();
    return new MongoDataKeyStore();
  });
});
