import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import {
  describePayloadReferencesContract,
  describePayloadStoreContract,
  describeTransactionalPayloadStoreContract,
} from './support/payload-store-contract';

describePayloadStoreContract('in-memory', async () => {
  const store = new InMemoryPayloadStore();
  return {
    store,
    remove: async (_orgId, ref) => {
      store.objects.delete(ref);
    },
  };
});

const inMemory = async () => {
  const store = new InMemoryPayloadStore();
  return {
    store,
    transact: <T>(_orgId: string, work: (tx: unknown) => Promise<T>) => work(undefined),
    isReferenced: async (_orgId: string, ref: string) => store.referenced.has(ref),
  };
};

describePayloadReferencesContract<unknown>('in-memory', inMemory);
describeTransactionalPayloadStoreContract<unknown>('in-memory', inMemory);
