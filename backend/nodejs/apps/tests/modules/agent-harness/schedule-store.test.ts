import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { describeScheduleStoreContract, describeScheduleStoreRaces } from './support/schedule-store-contract';
import { TestClock } from './support/schedules';

describeScheduleStoreContract('in-memory', async () => {
  const clock = new TestClock();
  return { store: new InMemoryScheduleStore(clock), clock };
});

describeScheduleStoreRaces('in-memory', async (clock) => {
  const store = new InMemoryScheduleStore(clock);
  return () => store;
});
