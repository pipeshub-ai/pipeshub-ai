import { metricsBackend } from '../metrics-backend';
import { SERVICE_NAME } from '../constants';

const httpRequests = metricsBackend.createCounter({
  name: 'pipeshub_http_requests_total',
  help: 'Total HTTP requests handled by the service',
  labelNames: ['service', 'route', 'method', 'status', 'org', 'domain'],
});

const httpRequestDuration = metricsBackend.createHistogram({
  name: 'pipeshub_http_request_duration_seconds',
  help: 'HTTP request latency in seconds',
  labelNames: ['service', 'route', 'method'],
  buckets: [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30],
});

export function recordHttpRequest(
  route: string,
  method: string,
  statusCode: number,
  orgId?: string,
  durationSeconds?: number,
  domain?: string,
): void {
  const labels = {
    service: SERVICE_NAME,
    route: route === '' ? 'unmatched' : route,
    method,
    status: String(statusCode),
    org: orgId == null || orgId === '' ? 'unknown' : orgId,
    domain: domain == null || domain === '' ? 'unknown' : domain,
  };
  httpRequests.inc(labels);
  if (typeof durationSeconds === 'number') {
    httpRequestDuration.observe(
      { service: SERVICE_NAME, route: labels.route, method },
      durationSeconds,
    );
  }
}
