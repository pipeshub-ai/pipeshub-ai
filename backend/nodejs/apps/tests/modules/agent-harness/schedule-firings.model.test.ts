/** QA13-10: schedule firings as a state machine, on the in-memory store (the Mongo twin is in `schedule-store.mongo.itest.ts`). */
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { describeScheduleFiringMachine } from './support/schedule-firing-machine';

describeScheduleFiringMachine('in-memory', async (clock) => new InMemoryScheduleStore(clock));
