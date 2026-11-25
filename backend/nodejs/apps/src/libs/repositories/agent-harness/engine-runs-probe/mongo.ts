import { WorkerFeatureAvailability } from '../../../../modules/agent-harness/application/worker-feature-availability';
import { isAgentHarnessInfrastructureReady } from '../../../../modules/agent-harness/config';
import { ENGINE_FEATURE } from '../../../../modules/agent-harness/domain/worker-compat';
import { MongoWorkerFeatureSightings } from '../worker-feature-sightings/mongo';

let availability: WorkerFeatureAvailability | null = null;

/**
 * Whether a worker that runs engine runs (coding agents) was seen lately
 * (UX3, R1 F16): the only use of personal AI accounts, so the settings page
 * offers them only then. Never throws; an unreadable store answers false.
 */
export const engineRunsServed = async (): Promise<boolean> => {
  if (!isAgentHarnessInfrastructureReady()) return false;
  availability ??= new WorkerFeatureAvailability({
    sightings: new MongoWorkerFeatureSightings(),
  });
  return availability.live(ENGINE_FEATURE);
};
