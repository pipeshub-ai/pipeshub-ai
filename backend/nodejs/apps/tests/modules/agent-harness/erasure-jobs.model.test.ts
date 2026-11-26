/** QA13-11: erasure jobs as a state machine, on the in-memory store (the Mongo twin is in `session-erasure.mongo.itest.ts`). */
import { InMemoryErasureJobStore } from '../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import { describeErasureJobMachine } from './support/erasure-job-machine';

describeErasureJobMachine('in-memory', async (clock) => new InMemoryErasureJobStore(() => clock.now));
