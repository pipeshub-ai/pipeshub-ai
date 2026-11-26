/** OPS1-2-3: whether this process prepared the harness's store, as Prometheus exposes it. */
import { expect } from 'chai';
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend';
import { storePreparedGauge } from '../../../src/modules/agent-harness/adapters/telemetry/store-preparation-metrics';

const SERIES = 'pipeshub_agent_harness_store_prepared';

const sample = async (): Promise<number | null> => {
  const line = (await metricsBackend.serialize()).split('\n').find((l) => l.startsWith(`${SERIES} `));
  return line === undefined ? null : Number(line.slice(SERIES.length + 1));
};

describe('store preparation metric (OPS1-2-3)', () => {
  it('is 0 while the harness is unavailable and 1 once its store is prepared', async () => {
    storePreparedGauge(false);
    expect(await sample()).to.equal(0);
    storePreparedGauge(true);
    expect(await sample()).to.equal(1);
  });
});
