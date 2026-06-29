import { metricsBackend } from '../metrics-backend';
import { SERVICE_NAME } from '../constants';

// Consent state per org
const metricCollectionEnabled = metricsBackend.createGauge({
  name: 'pipeshub_metric_collection_enabled',
  help: 'Metric collection consent (1 = enabled, 0 = disabled)',
  labelNames: ['service', 'org'],
});

export function setMetricCollectionEnabled(
  org: string,
  enabled: boolean,
): void {
  metricCollectionEnabled.set(
    { service: SERVICE_NAME, org: org === '' ? 'unknown' : org },
    enabled ? 1 : 0,
  );
}
