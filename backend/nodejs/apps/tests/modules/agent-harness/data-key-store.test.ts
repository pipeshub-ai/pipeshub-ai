/** The DataKeyStore contract on `InMemoryDataKeyStore`; Mongo runs it in data-key-store.mongo.itest.ts. */
import { InMemoryDataKeyStore } from '../../../src/modules/agent-harness/adapters/in-memory-data-key-store';
import { describeDataKeyStoreContract } from './support/data-key-store-contract';

describeDataKeyStoreContract('in-memory', async () => new InMemoryDataKeyStore());
