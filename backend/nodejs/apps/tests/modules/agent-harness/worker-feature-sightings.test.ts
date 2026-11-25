/** The WorkerFeatureSightings contract on `InMemoryWorkerFeatureSightings` (one process: every replica is the same object). */
import { InMemoryWorkerFeatureSightings } from '../../../src/modules/agent-harness/adapters/in-memory-worker-feature-sightings';
import { describeWorkerFeatureSightingsContract } from './support/worker-feature-sightings-contract';

describeWorkerFeatureSightingsContract('in-memory', async (clock) => {
  const sightings = new InMemoryWorkerFeatureSightings(clock);
  return { replica: () => sightings };
});
