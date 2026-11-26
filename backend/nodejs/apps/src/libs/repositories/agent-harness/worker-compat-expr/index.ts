/**
 * `domain/worker-compat` in each backend's query language (aggregation expressions, SQL), used inside that backend's
 * execution stores: nothing picks one alone.
 */
export * as mongoWorkerCompat from './mongo';
export * as pgWorkerCompat from './pg';
