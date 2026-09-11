import { isElectron } from '@/lib/electron';
import { ConnectorsApi } from '../api';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import { useConnectorsStore } from '../store';
import type { ConnectorInstance } from '../types';
import {
  isLocalFsConnectorType,
  readDesktopRefusal,
  type DesktopRefusalReason,
} from './local-fs-helpers';
import {
  buildLocalSyncStartOptionsFromConnectorConfig,
  checkLocalRootPathConflict,
  extractLocalFsRootPath,
  startElectronLocalSync,
} from './electron-local-sync';
import { refreshConnectorInstanceDetails } from './refresh-instance-details';

/**
 * Where the sync was performed. Every connector — Local FS included — goes
 * through the backend: the connector service runs `run_sync` and pulls file
 * events from the desktop over the socket relay, so pressing Sync from a
 * browser works as long as the user's desktop app is running. Node checks the
 * socket claim before queueing the job and refuses with `requires-desktop`
 * when no desktop holds it.
 */
export type ResyncOutcome =
  | { kind: 'backend' }
  | { kind: 'requires-desktop'; reason: DesktopRefusalReason };

function isIdleSyncStatus(status?: string | null): boolean {
  const normalized = (status ?? CONNECTOR_INSTANCE_STATUS.IDLE).toUpperCase();
  return normalized === CONNECTOR_INSTANCE_STATUS.IDLE;
}

function persistConnectorSyncStatus(connectorId: string, status: string): void {
  const state = useConnectorsStore.getState();
  const existing =
    state.activeConnectors.find((c) => c._key === connectorId) ??
    state.instances.find((c) => c._key === connectorId) ??
    (state.selectedInstance?._key === connectorId ? state.selectedInstance : undefined);

  if (!existing) {
    return;
  }

  state.upsertConnectorInstance({
    ...existing,
    status,
  } as ConnectorInstance);
}

/** Optimistic in-progress status, refetch row, then re-apply if GET is still IDLE. */
async function applyPostResyncInstanceRefresh(
  connectorId: string,
  fullSync: boolean
): Promise<void> {
  const expectedStatus = fullSync
    ? CONNECTOR_INSTANCE_STATUS.FULL_SYNCING
    : CONNECTOR_INSTANCE_STATUS.SYNCING;

  persistConnectorSyncStatus(connectorId, expectedStatus);

  await refreshConnectorInstanceDetails(connectorId);

  const state = useConnectorsStore.getState();
  const row =
    state.activeConnectors.find((c) => c._key === connectorId) ??
    state.instances.find((c) => c._key === connectorId);

  if (isIdleSyncStatus(row?.status)) {
    persistConnectorSyncStatus(connectorId, expectedStatus);
  }
}

/**
 * Preflight for activating a Local FS connector (toggle sync on / "Start
 * Syncing" from the create dialog): reject *before* the backend flips the
 * connector active if another connector already watches the same root.
 * Without this, activation succeeds, the watcher-start that follows fails,
 * and the instance is left active with no watcher and a hard-to-diagnose
 * error further down the flow. No-op for non-Local-FS types and outside
 * Electron (nothing client-side to conflict with).
 */
export async function assertLocalFsRootPathAvailable(
  connectorId: string,
  connectorType: string
): Promise<void> {
  if (!isLocalFsConnectorType(connectorType) || !isElectron()) return;

  const config =
    useConnectorsStore.getState().instanceConfigs[connectorId] ??
    (await ConnectorsApi.getConnectorConfig(connectorId));

  const rootPath = extractLocalFsRootPath(config);
  if (!rootPath) return;

  const result = await checkLocalRootPathConflict(connectorId, rootPath);
  if (!result.available) {
    const owner = result.ownerConnectorName || result.ownerConnectorId || 'another connector';
    throw new Error(`Local sync root is already watched by connector "${owner}": ${rootPath}`);
  }
}

/**
 * Mounts the Electron watcher and waits until the desktop has claimed this
 * connector on the socket. Node refuses toggle-on and resync while no claim
 * exists, so this must complete first. No-op outside Electron. Idempotent —
 * `LocalSyncManager.start` returns early for an unchanged config.
 */
export async function ensureLocalWatcherStarted(
  connectorId: string,
  connectorType: string
): Promise<void> {
  if (!isLocalFsConnectorType(connectorType) || !isElectron()) return;
  const config =
    useConnectorsStore.getState().instanceConfigs[connectorId] ??
    (await ConnectorsApi.getConnectorConfig(connectorId));

  const rootPath = extractLocalFsRootPath(config);
  if (!rootPath) return;

  const instance =
    useConnectorsStore.getState().activeConnectors.find((c) => c._key === connectorId) ??
    useConnectorsStore.getState().instances.find((c) => c._key === connectorId);

  await startElectronLocalSync({
    connectorId,
    connectorName: instance?.name ?? connectorId,
    rootPath,
    ...buildLocalSyncStartOptionsFromConnectorConfig(config, connectorType),
  });
}

/**
 * Preflight + watcher claim for turning a Local FS connector on. Must run
 * *before* `toggleConnector`, which publishes `appEnabled` with an immediate
 * sync. No-op for other types and outside Electron.
 */
export async function prepareLocalFsForEnable(
  connectorId: string,
  connectorType: string
): Promise<void> {
  await assertLocalFsRootPathAvailable(connectorId, connectorType);
  if (!isLocalFsConnectorType(connectorType)) return;
  await ensureLocalWatcherStarted(connectorId, connectorType);
}

export async function runConnectorResync(args: {
  connectorId: string;
  connectorType: string;
  fullSync?: boolean;
}): Promise<ResyncOutcome> {
  const { connectorId, connectorType, fullSync = false } = args;
  const localFs = isLocalFsConnectorType(connectorType);
  if (localFs) {
    try {
      await ensureLocalWatcherStarted(connectorId, connectorType);
    } catch (error) {
      // The backend run can still succeed via a lazy mount, so a failed
      // pre-warm must not block the sync.
      console.warn('[local-sync] could not pre-mount watcher before resync:', error);
    }
  }
  try {
    await ConnectorsApi.resyncConnector(connectorId, connectorType, fullSync);
  } catch (error) {
    const reason = localFs ? readDesktopRefusal(error) : null;
    if (reason) {
      return { kind: 'requires-desktop', reason };
    }
    throw error;
  }
  await applyPostResyncInstanceRefresh(connectorId, fullSync);
  return { kind: 'backend' };
}

/**
 * Turn sync on. Runs the Local FS preflight + watcher claim first, then the
 * toggle; Node refuses a Local FS enable when no desktop holds the claim
 * (DESKTOP_OFFLINE, or DESKTOP_UNCLAIMED when a desktop is connected but has
 * never enabled this connector), reported as `requires-desktop` with the
 * reason instead of thrown. Does not refresh the row — callers do that.
 */
export async function toggleConnectorSyncOn(
  connectorId: string,
  connectorType?: string
): Promise<ResyncOutcome> {
  const localFs = !!connectorType && isLocalFsConnectorType(connectorType);
  if (connectorType) {
    await prepareLocalFsForEnable(connectorId, connectorType);
  }
  try {
    await ConnectorsApi.toggleConnector(connectorId, 'sync');
  } catch (error) {
    const reason = localFs ? readDesktopRefusal(error) : null;
    if (reason) {
      return { kind: 'requires-desktop', reason };
    }
    throw error;
  }
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
): Promise<ResyncOutcome> {
  if (!instance._key) {
    throw new Error('startConnectorSync: connectorId (_key) is required');
  }
  const fresh = await ConnectorsApi.getConnectorInstance(instance._key);
  const type = fresh.type || instance.type;
  if (!fresh.isActive) {
    const outcome = await toggleConnectorSyncOn(instance._key, type);
    if (outcome.kind === 'backend') {
      await refreshConnectorInstanceDetails(instance._key);
    }
    return outcome;
  }
  if (!type) {
    throw new Error(
      `startConnectorSync: connector type unknown for instance ${instance._key}`
    );
  }
  return runConnectorResync({ connectorId: instance._key, connectorType: type });
}
