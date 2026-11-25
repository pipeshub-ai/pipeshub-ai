import { InMemoryLiveBus } from '../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import { describeLiveBusContract } from './support/live-bus-contract';

describeLiveBusContract('in-memory', async () => {
  const bus = new InMemoryLiveBus();
  return {
    bus,
    publish: async (delta) => bus.publish(delta),
    expire: async (runId) => bus.expire(runId),
    unreachable: async () => {
      const broken = new InMemoryLiveBus();
      broken.failing = new Error('live bus unreachable');
      return broken;
    },
  };
});
