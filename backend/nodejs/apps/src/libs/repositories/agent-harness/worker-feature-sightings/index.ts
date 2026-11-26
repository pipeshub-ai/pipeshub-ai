import type { WorkerFeatureSightings } from '../../../../modules/agent-harness/ports/worker-features';
import { pickHarnessStore } from '../stores';
import { MongoWorkerFeatureSightings } from './mongo';
import { PgWorkerFeatureSightings } from './pg';

export const createWorkerFeatureSightings = (): WorkerFeatureSightings =>
  pickHarnessStore<WorkerFeatureSightings>(
    'agent-harness/worker-feature-sightings',
    {
      mongo: () => new MongoWorkerFeatureSightings(),
      postgres: () => new PgWorkerFeatureSightings(),
    },
  );
