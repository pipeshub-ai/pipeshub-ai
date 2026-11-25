/**
 * DUR2: what an operator watches about worker features, as Prometheus
 * exposes it: whether a live worker advertises a feature, runs admitted
 * without `run_code`, and stranded runs by outcome.
 */
import { expect } from 'chai';
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend';
import { prometheusWorkerFeatureMetrics } from '../../../src/modules/agent-harness/adapters/telemetry/worker-feature-metrics';

const sample = async (series: string): Promise<number | null> => {
  const text = await metricsBackend.serialize();
  const line = text.split('\n').find((l) => l.startsWith(`${series} `));
  return line === undefined ? null : Number(line.slice(series.length + 1));
};

describe('worker feature metrics (DUR2)', () => {
  it('reports a feature live as 1 and gone as 0', async () => {
    const series = 'pipeshub_agent_harness_worker_feature_live{feature="agent_code"}';
    prometheusWorkerFeatureMetrics.live('agent_code', true);
    expect(await sample(series)).to.equal(1);
    prometheusWorkerFeatureMetrics.live('agent_code', false);
    expect(await sample(series)).to.equal(0);
  });

  it('counts withheld admissions per feature', async () => {
    const series = 'pipeshub_agent_harness_agent_code_withheld_total{feature="agent_code"}';
    const before = (await sample(series)) ?? 0;
    prometheusWorkerFeatureMetrics.withheld('agent_code');
    prometheusWorkerFeatureMetrics.withheld('agent_code');
    expect(await sample(series)).to.equal(before + 2);
  });

  it('counts stranded runs by feature and outcome, separately', async () => {
    const relaxed =
      'pipeshub_agent_harness_stranded_runs_total{feature="agent_code",outcome="relaxed"}';
    const failed =
      'pipeshub_agent_harness_stranded_runs_total{feature="agent_code",outcome="failed"}';
    const relaxedBefore = (await sample(relaxed)) ?? 0;
    const failedBefore = (await sample(failed)) ?? 0;
    prometheusWorkerFeatureMetrics.stranded('agent_code', 'relaxed');
    expect(await sample(relaxed)).to.equal(relaxedBefore + 1);
    expect((await sample(failed)) ?? 0).to.equal(failedBefore);
    prometheusWorkerFeatureMetrics.stranded('agent_code', 'failed');
    expect(await sample(failed)).to.equal(failedBefore + 1);
  });
});
