/**
 * The AuditChainStore and AuditChainQueries contracts on `MongoAuditChainStore` in durable
 * transactions on a replica set (run as execution-store.mongo.itest.ts says).
 */
import 'reflect-metadata';
import { ClientSession } from 'mongoose';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { describeAuditChainStoreContract } from './support/audit-chain-store-contract';
import { describeAuditChainQueriesContract } from './support/audit-chain-queries-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  resetHarnessMongo,
} from './support/harness-mongo';

describe('AuditChainStore on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  const mongo = async () => {
    await resetHarnessMongo();
    return {
      store: new MongoAuditChainStore(),
      transact: <T>(work: (tx: ClientSession) => Promise<T>) => inMongoTransaction(work),
      atomic: true,
    };
  };
  describeAuditChainStoreContract<ClientSession>('mongo replica set', mongo);
  describeAuditChainQueriesContract<ClientSession>('mongo replica set', mongo);
});
