import { metricsBackend } from '../services/telemetry/metrics-backend';

export const docstoreMetrics = {
  nulStripped: metricsBackend.createCounter({
    name: 'docstore_nul_stripped_total',
    help: 'U+0000 characters removed from strings and keys before a write',
    labelNames: ['repo', 'field'],
  }),
  fenceRefused: metricsBackend.createCounter({
    name: 'docstore_fence_refused_total',
    help: 'Writes refused by the store fence',
    labelNames: ['store', 'reason'],
  }),
  fenceCheck: metricsBackend.createHistogram({
    name: 'docstore_fence_check_seconds',
    help: 'Time spent reading the Mongo fence before a write',
    labelNames: [],
    buckets: [0.0005, 0.001, 0.005, 0.01, 0.05, 0.2, 1, 2],
  }),
  rlsViolation: metricsBackend.createCounter({
    name: 'docstore_rls_violation_total',
    help: 'Writes the database refused for privilege or row policy (a wrong-org bug); alert on > 0',
    labelNames: [],
  }),
  searchTruncated: metricsBackend.createCounter({
    name: 'docstore_chat_search_truncated_total',
    help: 'Chat content searches cut short (a very large history, or more matches than the limit): older matches may be missing',
    labelNames: [],
  }),
  failedTransaction: metricsBackend.createCounter({
    name: 'docstore_failed_transaction_total',
    help: 'Statements run after an error inside a unit of work (a swallowed error without a savepoint)',
    labelNames: [],
  }),
  shadowReads: metricsBackend.createCounter({
    name: 'docstore_shadow_reads_total',
    help: 'Reads of the shadow check (DOC_STORE=shadow) by repository and result: equal, diff, timeout, error',
    labelNames: ['repo', 'result'],
  }),
  ttlSwept: metricsBackend.createCounter({
    name: 'docstore_ttl_swept_total',
    help: 'Rows the TTL sweeper removed (expired tokens and codes, notifications past retention, delivered outbox rows)',
    labelNames: ['target'],
  }),
  ttlSweepFailed: metricsBackend.createCounter({
    name: 'docstore_ttl_sweep_failed_total',
    help: 'TTL sweeps of one table that failed',
    labelNames: ['target'],
  }),
};
