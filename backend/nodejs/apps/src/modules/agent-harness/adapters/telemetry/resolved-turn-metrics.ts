import { metricsBackend } from '../../../../libs/services/telemetry/metrics-backend';
import type { ResolvedTurnMetrics } from '../../ports/resolved-turn-metrics';

const trimmed = metricsBackend.createCounter({
  name: 'pipeshub_cp_resolved_turn_trimmed_total',
  help: 'Legacy chat turns whose X-PipesHub-Resolved header was sent without its bindings because it would not fit (CPD-10)',
  labelNames: [],
});

export const prometheusResolvedTurnMetrics: ResolvedTurnMetrics = {
  trimmed: () => {
    trimmed.inc();
  },
};
