import { metricsBackend } from '../metrics-backend';
import { SERVICE_NAME } from '../constants';

// Same metric and label set as the Python services' counter
// (app/telemetry/modules/activity_metrics.py), so one query in Grafana sees
// activations from both. Labels are bounded: no user id or address.
const activity = metricsBackend.createCounter({
  name: 'pipeshub_activity_total',
  help: 'Total service activities recorded',
  labelNames: [
    'service',
    'activity',
    'connector',
    'status',
    'org',
    'kb',
    'domain',
    'mimetype',
  ],
});

export function recordServiceActivity(
  activityName: string,
  labels: { status?: string; org?: string; domain?: string } = {},
): void {
  const org = labels.org ?? '';
  const domain = labels.domain ?? '';
  activity.inc({
    service: SERVICE_NAME,
    activity: activityName,
    connector: 'none',
    status: labels.status ?? 'ok',
    org: org === '' ? 'unknown' : org,
    kb: 'none',
    domain: domain === '' ? 'unknown' : domain,
    mimetype: 'none',
  });
}
