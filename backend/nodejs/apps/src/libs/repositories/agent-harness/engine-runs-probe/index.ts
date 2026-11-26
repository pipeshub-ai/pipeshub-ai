import { pickHarnessStore } from '../stores';
import { engineRunsServed as mongoEngineRunsServed } from './mongo';
import { pgEngineRunsServed } from './pg';

/** Whether a worker that runs engine runs was seen lately, on the selected document store; never throws. */
export const engineRunsServed = (): Promise<boolean> =>
  pickHarnessStore<() => Promise<boolean>>('agent-harness/engine-runs-probe', {
    mongo: () => mongoEngineRunsServed,
    postgres: () => pgEngineRunsServed,
  })();
