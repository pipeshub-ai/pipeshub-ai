/** The ErasureJobStore contract on `InMemoryErasureJobStore`; Mongo runs it in erasure-job-store.mongo.itest.ts. */
import { InMemoryErasureJobStore } from '../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import { describeErasureJobStoreContract } from './support/erasure-job-store-contract';

describeErasureJobStoreContract('in-memory', async (clock) => new InMemoryErasureJobStore(() => clock.now()));
