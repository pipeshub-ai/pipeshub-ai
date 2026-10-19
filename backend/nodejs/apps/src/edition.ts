/**
 * Edition constants that must stay import-free.
 *
 * This is the only edition switch that `libs/services/telemetry` may import:
 * the metrics backend is chosen at module load, and `config.ts` re-exports
 * routes that import telemetry, so importing from there would cycle.
 */

// Used when TELEMETRY_COLLECTION_FLAG is unset.
export const TELEMETRY_DEFAULT_ENABLED = true;

/**
 * Whether the admin routes that drop and rebuild the shared records vector
 * collection are offered. A deployment where one collection holds more than
 * one workspace's records turns this off, and the routes then answer 403.
 */
export const VECTOR_STORE_REBUILD_AVAILABLE = true;
