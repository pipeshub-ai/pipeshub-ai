import { opsMetrics } from '../../../../libs/metrics';

const prepared = opsMetrics.createGauge({
  name: 'pipeshub_agent_harness_store_prepared',
  help: "Whether this process prepared the agent harness's store (1) or runs with the harness unavailable while it retries (0)",
  labelNames: [],
});

export const storePreparedGauge = (value: boolean): void => {
  prepared.set({}, value ? 1 : 0);
};
