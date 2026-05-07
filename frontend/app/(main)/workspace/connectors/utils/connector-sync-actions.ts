import { isElectron } from '@/lib/electron';
import { ConnectorsApi } from '../api';
import type { ConnectorInstance } from '../types';
import { isLocalFsConnectorType } from './local-fs-helpers';
import { fullResyncElectronLocalSync } from './electron-local-sync';

/**
 * Where the resync was actually performed. Local-FS connectors are
 * client-managed: the desktop app owns the watcher and the file scan, so the
 * backend has no work to do. The web build can't drive that sync from
 * elsewhere, so it returns `requires-desktop` and lets the caller surface a
 * "open the desktop app" message.
 */
export type ResyncOutcome =
  | { kind: 'electron-local' }
  | { kind: 'backend' }
  | { kind: 'requires-desktop' };

export async function runConnectorResync(args: {
  connectorId: string;
  connectorType: string;
  fullSync?: boolean;
}): Promise<ResyncOutcome> {
  const { connectorId, connectorType, fullSync = false } = args;
  if (isLocalFsConnectorType(connectorType)) {
    if (!isElectron()) {
      return { kind: 'requires-desktop' };
    }
    await fullResyncElectronLocalSync(connectorId);
    return { kind: 'electron-local' };
  }
  await ConnectorsApi.resyncConnector(connectorId, connectorType, fullSync);
  return { kind: 'backend' };
}

/**
 * Single entry point for "make this instance sync now".
 * Re-fetches the instance so `isActive` is never read from stale client state.
 * - Inactive → toggle sync ON; backend publishes `appEnabled` with `syncAction:"immediate"`.
 * - Active   → resync (kick a new sync job on the already-enabled connector).
 * Matches the legacy frontend: never chains toggle + resync in one action.
 */
export async function startConnectorSync(
  instance: { _key: string } & Partial<Pick<ConnectorInstance, 'type'>>
): Promise<ResyncOutcome | null> {
  if (!instance._key) {
    throw new Error('startConnectorSync: connectorId (_key) is required');
  }
  const fresh = await ConnectorsApi.getConnectorInstance(instance._key);
  if (!fresh.isActive) {
    await ConnectorsApi.toggleConnector(instance._key, 'sync');
    return null;
  }
  const type = fresh.type || instance.type;
  if (!type) {
    throw new Error(
      `startConnectorSync: connector type unknown for instance ${instance._key}`
    );
  }
  return runConnectorResync({ connectorId: instance._key, connectorType: type });
}
