/** P7.3 MS: the memory store contract on the in-memory store. */
import { InMemoryMemoryStore } from '../../../src/modules/agent-harness/adapters/in-memory-memory-store';
import { describeMemoryStoreContract } from './support/memory-store-contract';

describeMemoryStoreContract('in-memory', async () => new InMemoryMemoryStore());
