import { metricsBackend } from '../../../../libs/services/telemetry/metrics-backend';
import { WorkerFeature } from '../../domain/worker-compat';
import {
  StrandedOutcome,
  WorkerFeatureMetrics,
} from '../../ports/worker-features';

const live = metricsBackend.createGauge({
  name: 'pipeshub_agent_harness_worker_feature_live',
  help: 'Whether a live agent-harness worker advertises the feature (1) or none does (0)',
  labelNames: ['feature'],
});

const withheld = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_agent_code_withheld_total',
  help: 'Agent runs admitted without run_code because no live worker advertises agent_code',
  labelNames: ['feature'],
});

const stranded = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_stranded_runs_total',
  help: 'Queued runs whose required feature no live worker advertises: relaxed (served without it) or failed',
  labelNames: ['feature', 'outcome'],
});

export const prometheusWorkerFeatureMetrics: WorkerFeatureMetrics = {
  live: (feature: WorkerFeature, isLive: boolean) => {
    live.set({ feature }, isLive ? 1 : 0);
  },
  withheld: (feature: WorkerFeature) => {
    withheld.inc({ feature });
  },
  stranded: (feature: WorkerFeature, outcome: StrandedOutcome) => {
    stranded.inc({ feature, outcome });
  },
};
