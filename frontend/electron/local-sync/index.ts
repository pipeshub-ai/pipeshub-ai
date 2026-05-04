/**
 * Public surface of the local-sync module. Consumers (Electron main, tests)
 * should import from here rather than reaching into subdirectories so internal
 * reorganization stays invisible.
 */
export { LocalSyncManager } from './manager';
export type {
  ConnectorStatus,
  StartArgs,
  ReplayResult,
  LocalSyncManagerOptions,
} from './manager';
