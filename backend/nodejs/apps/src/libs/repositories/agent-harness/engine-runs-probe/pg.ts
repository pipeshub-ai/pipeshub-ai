import { WorkerFeatureAvailability } from '../../../../modules/agent-harness/application/worker-feature-availability';
import { isAgentHarnessInfrastructureReady } from '../../../../modules/agent-harness/config';
import { ENGINE_FEATURE } from '../../../../modules/agent-harness/domain/worker-compat';
import { PgWorkerFeatureSightings } from '../worker-feature-sightings/pg';

let availability: WorkerFeatureAvailability | null = null;

/** `engineRunsServed` on PostgreSQL: whether a worker that runs engine runs was seen lately. Never throws. */
export const pgEngineRunsServed = async (): Promise<boolean> => {
  if (!isAgentHarnessInfrastructureReady()) return false;
  availability ??= new WorkerFeatureAvailability({
    sightings: new PgWorkerFeatureSightings(),
  });
  return availability.live(ENGINE_FEATURE);
};
