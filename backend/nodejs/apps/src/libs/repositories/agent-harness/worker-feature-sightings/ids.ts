import type { WorkerFeature } from '../../../../modules/agent-harness/domain/worker-compat';

/** One liveness record per feature, beside the control plane's own (`agentHarnessLiveness`, `harness.liveness`). */
export const featureLivenessId = (feature: WorkerFeature): string =>
  `worker-feature:${feature}`;
