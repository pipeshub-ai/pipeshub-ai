import { InMemoryRunWakeup } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { describeRunWakeupContract, wakeCounter } from './support/run-wakeup-contract';

describeRunWakeupContract('in-memory', async () => {
  const holders = new Map<string, Set<ReturnType<typeof wakeCounter>>>();
  const wakeup = new InMemoryRunWakeup((runId) => {
    for (const holder of holders.get(runId) ?? []) holder.wake();
  });
  return {
    wakeup,
    hold: async (runId) => {
      const set = holders.get(runId) ?? new Set();
      holders.set(runId, set);
      const holder = wakeCounter(async () => {
        set.delete(holder);
      });
      set.add(holder);
      return holder;
    },
  };
});
