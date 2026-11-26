import * as http from 'http';
import { performance, type EventLoopUtilization } from 'perf_hooks';
import * as promClient from 'prom-client';

export const EVENT_LOOP_UTILIZATION_METRIC =
  'pipeshub_nodejs_event_loop_utilization';

/**
 * The process's own saturation, for a scraper and the autoscaler (PERF-E).
 * Node serves the API on one thread, so pod CPU against a multi-core request
 * never shows it full; the share of time its event loop was busy does. Kept
 * out of the product telemetry registry: an autoscaling signal must not
 * depend on the telemetry switch, and carries no org or user labels.
 */
export function createProcessMetricsRegistry(): promClient.Registry {
  const registry = new promClient.Registry();
  let last: EventLoopUtilization = performance.eventLoopUtilization();
  new promClient.Gauge({
    name: EVENT_LOOP_UTILIZATION_METRIC,
    help: 'Share of wall time the event loop was busy since the previous scrape (0 to 1)',
    registers: [registry],
    collect() {
      const now = performance.eventLoopUtilization();
      this.set(performance.eventLoopUtilization(now, last).utilization);
      last = now;
    },
  });
  return registry;
}

/** `GET /metrics` in Prometheus' text format; nothing else is served. */
export function createProcessMetricsServer(
  registry: promClient.Registry = createProcessMetricsRegistry(),
): http.Server {
  return http.createServer((req, res) => {
    if (req.method !== 'GET' || req.url?.split('?')[0] !== '/metrics') {
      res.writeHead(404).end();
      return;
    }
    registry.metrics().then(
      (body) => {
        res.writeHead(200, { 'Content-Type': registry.contentType }).end(body);
      },
      () => {
        res.writeHead(500).end();
      },
    );
  });
}
