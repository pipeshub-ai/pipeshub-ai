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

// The agent session API's availability SLI (docs/agent-harness-v2/SLO.md, "What available means"): a 5xx there
// spends the error budget. Named in the harness's family so Node's merged metrics route passes it; no org label.
const sessionApiRequests = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_session_api_requests_total',
  help: 'Requests to the agent session API by surface and outcome (server_error: a 5xx answer)',
  labelNames: ['surface', 'outcome'],
});

const SESSION_API_SURFACES: ReadonlyArray<[string, RegExp]> = [
  ['conversation_runs', /^\/api\/v1\/conversations\/[^/]+\/runs(\/|$)/],
  ['agent_conversations', /^\/api\/v1\/agents\/[^/]+\/conversations(\/|$)/],
  ['agent_harness', /^\/api\/v1\/agent-harness\/(?!internal(\/|$))/],
];

/** The session API surface a route template belongs to, or null for every other route. */
export function sessionApiSurface(route: string): string | null {
  return (
    SESSION_API_SURFACES.find(([, pattern]) => pattern.test(route))?.[0] ?? null
  );
}

// Unbounded route values grow the push payload until the collector rejects it.
const MAX_UNIQUE_ROUTES = 300;
const seenRoutes = new Set<string>();

function boundRoute(route: string): string {
  if (seenRoutes.has(route)) {
    return route;
  }
  if (seenRoutes.size >= MAX_UNIQUE_ROUTES) {
    return 'other';
  }
  seenRoutes.add(route);
  return route;
}

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
    route: boundRoute(route === '' ? 'unmatched' : route),
    method,
    status: String(statusCode),
    org: orgId == null || orgId === '' ? 'unknown' : orgId,
    domain: domain == null || domain === '' ? 'unknown' : domain,
  };
  httpRequests.inc(labels);
  const surface = sessionApiSurface(route);
  if (surface !== null) {
    sessionApiRequests.inc({
      surface,
      outcome: statusCode >= 500 ? 'server_error' : 'ok',
    });
  }
  if (typeof durationSeconds === 'number') {
    httpRequestDuration.observe(
      { service: SERVICE_NAME, route: labels.route, method },
      durationSeconds,
    );
  }
}
