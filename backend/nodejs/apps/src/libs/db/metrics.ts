import { metricsBackend } from '../services/telemetry/metrics-backend';

const buckets = [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 15, 60];

export const metrics = {
  query: metricsBackend.createHistogram({
    name: 'pipeshub_db_query_seconds',
    help: 'Statement duration by call site',
    labelNames: ['service', 'site', 'class'],
    buckets,
  }),
  uow: metricsBackend.createHistogram({
    name: 'pipeshub_db_uow_seconds',
    help: 'Unit of work duration, retries included',
    labelNames: ['service', 'outcome'],
    buckets,
  }),
  poolWait: metricsBackend.createHistogram({
    name: 'pipeshub_db_pool_wait_seconds',
    help: 'Time waiting for a pool connection',
    labelNames: [],
    buckets,
  }),
  retries: metricsBackend.createCounter({
    name: 'pipeshub_db_retries_total',
    help: 'Unit of work retries by cause',
    labelNames: ['code'],
  }),
  roleLeak: metricsBackend.createCounter({
    name: 'pipeshub_db_session_role_leak_total',
    help: 'Connections that arrived with a session role already set',
    labelNames: [],
  }),
  orgContextMissing: metricsBackend.createCounter({
    name: 'pipeshub_org_context_missing_total',
    help: 'Units of work refused for lack of an org context',
    labelNames: [],
  }),
};
