/**
 * The PayloadStore and PayloadReferences contracts on the production stores:
 * MongoDB (a real replica set, MONGO_IT_URI) and platform blob storage through
 * `LocalStorageAdapter` on a real local disk.
 */
import 'reflect-metadata';
import fs from 'node:fs';
import { ClientSession } from 'mongoose';
import { MongoPayloadStore, mongoPayloadId } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { AgentJournalPayloadModel } from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { StoragePayloadStore } from '../../../src/modules/agent-harness/adapters/storage/storage-payload-store';
import LocalStorageAdapter from '../../../src/modules/storage/providers/local-storage.provider';
import { describePayloadReferencesContract, describePayloadStoreContract, describeTransactionalPayloadStoreContract } from './support/payload-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo } from './support/harness-mongo';
import { filesNaming, isolatedHome, LOCAL_STORAGE_MOUNT } from './support/local-storage-service';

describe('journal payloads on the production stores (itest)', function () {
  this.timeout(60_000);

  describe('MongoDB replica set', () => {
    before(connectHarnessMongo);
    after(disconnectHarnessMongo);

    describePayloadStoreContract(
      'MongoPayloadStore',
      async () => ({
        store: new MongoPayloadStore(),
        remove: async (orgId, ref) => {
          await AgentJournalPayloadModel.deleteOne({ _id: mongoPayloadId(orgId, ref) });
        },
      }),
      { carriedName: (ref) => ref.replace(/^mongo:/, 'pg:') },
    );

    const mongo = async () => ({
      store: new MongoPayloadStore(),
      transact: <T>(_orgId: string, work: (session: ClientSession) => Promise<T>) => inMongoTransaction(work),
      isReferenced: async (orgId: string, ref: string) =>
        (await AgentJournalPayloadModel.findOne({ _id: mongoPayloadId(orgId, ref) }).lean())?.referenced === true,
    });
    describePayloadReferencesContract<ClientSession>('MongoPayloadStore', mongo, { transactional: true });
    describeTransactionalPayloadStoreContract<ClientSession>('MongoPayloadStore', mongo, { transactional: true });
  });

  describe('platform storage, local backend', () => {
    let home: ReturnType<typeof isolatedHome>;
    const mount = () => `${home.home}/.local/${LOCAL_STORAGE_MOUNT}`;
    const localStore = () =>
      new StoragePayloadStore(
        async () => new LocalStorageAdapter({ mountName: LOCAL_STORAGE_MOUNT, baseUrl: 'http://storage.internal' }),
        'local',
      );

    before(() => {
      home = isolatedHome();
    });
    after(() => home.restore());

    describePayloadStoreContract(
      'StoragePayloadStore',
      async () => ({
        store: localStore(),
        remove: async (_orgId, ref) => {
          for (const file of filesNaming(mount(), ref.split('/').pop()!)) fs.rmSync(file);
        },
      }),
      {
        unavailable: async () => {
          // A mount the process may no longer read or write: a lost volume, a permissions change.
          fs.chmodSync(mount(), 0o000);
          return { store: localStore(), restore: async () => fs.chmodSync(mount(), 0o755) };
        },
      },
    );
  });
});
