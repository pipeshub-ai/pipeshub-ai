/**
 * Public surface of the local-sync module. Consumers (Electron main, tests)
 * should import from here rather than reaching into subdirectories so internal
 * reorganization stays invisible.
 */
export { LocalSyncManager } from './manager';
export type {
  BootstrapResult,
  ConnectorStatus,
  StartArgs,
  LocalSyncManagerOptions,
} from './manager';
export { DesktopCredentialsStore } from './persistence/credentials';
export type { DesktopAccessTokenInput, SetAccessTokenResult } from './persistence/credentials';
export { DeviceIdentityError, resolveDeviceIdentity } from './persistence/device-identity';
export type { DeviceIdentity } from './persistence/device-identity';
export { DesktopSocketClient } from './transport/desktop-socket';
export { ContentStreamer } from './transport/content-streamer';
export type {
  ServePullRequest,
  ServePullResponse,
  SyncMode,
} from './pull-responder-types';
