import { metricsBackend } from '../metrics-backend';

const storeFallbacks = metricsBackend.createCounter({
  name: 'rate_limit_store_fallback_total',
  help: 'Rate-limit hits counted in-process because the shared store failed',
  labelNames: ['prefix'],
});

export function recordRateLimitStoreFallback(prefix: string): void {
  storeFallbacks.inc({ prefix });
}
