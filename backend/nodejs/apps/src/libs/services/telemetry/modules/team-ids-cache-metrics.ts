import { metricsBackend } from '../metrics-backend';

export type TeamIdsCacheResult = 'hit' | 'miss' | 'coalesced' | 'error';

const teamIdsCache = metricsBackend.createCounter({
  name: 'collab_teamids_cache_total',
  help: 'Caller team-id cache lookups by result',
  labelNames: ['result'],
});

export function recordTeamIdsCache(result: TeamIdsCacheResult): void {
  teamIdsCache.inc({ result });
}

export type TeamIdsUpstreamResult = 'ok' | 'error';

const teamIdsUpstream = metricsBackend.createHistogram({
  name: 'collab_teamids_upstream_seconds',
  help: 'Latency of upstream caller team-id lookups (cache misses and pass-throughs)',
  labelNames: ['result'],
  buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
});

export function recordTeamIdsUpstream(
  result: TeamIdsUpstreamResult,
  seconds: number,
): void {
  teamIdsUpstream.observe({ result }, seconds);
}
