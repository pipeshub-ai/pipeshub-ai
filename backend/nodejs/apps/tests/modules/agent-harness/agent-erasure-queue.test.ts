import { InMemoryAgentErasureQueue } from '../../../src/modules/agent-harness/adapters/in-memory-agent-erasures';
import { describeAgentErasureQueueContract } from './support/agent-erasure-queue-contract';

describeAgentErasureQueueContract('in-memory', async () => {
  let clock = 1_000;
  const tick = () => {
    clock += 1;
    return clock;
  };
  return { queue: new InMemoryAgentErasureQueue(tick), now: async () => clock + 1_000 };
});
