import { isElectron } from '@/lib/electron';
import { i18n } from '@/lib/i18n';
import { ConnectorsApi } from '../api';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import { useConnectorsStore } from '../store';
import type { ConnectorInstance } from '../types';
import {
  isLocalFsConnectorType,
  readDesktopRefusal,
  type DesktopRefusal,
} from './local-fs-helpers';
import {
  buildLocalSyncStartOptionsFromConnectorConfig,
  checkLocalRootPathConflict,
  extractLocalFsRootPath,
  getElectronDeviceInfo,
  startElectronLocalSync,
  stopElectronLocalSync,
} from './electron-local-sync';
import { refreshConnectorInstanceDetails } from './refresh-instance-details';

/**
 * Where the sync was performed. Every connector — Local FS included — goes
 * through the backend: the connector service runs `run_sync` and pulls file
 * events from the desktop over the socket relay, so pressing Sync from a
 * browser works as long as the connector's owner device is running the desktop
 * app. Node checks that device's socket before queueing the job and refuses
 * with `requires-desktop` when it is not connected.
 */
export type ResyncOutcome =
  | { kind: 'backend' }
  | ({ kind: 'requires-desktop' } & DesktopRefusal);

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
    throw new Error(`Local sync root is already synced by connector "${owner}": ${rootPath}`);
  }
}

/**
 * Mounts the Electron watcher so the journal is warm before the server's
 * first pull. Skipped for a connector owned by another device, which answers
 * its pulls instead. No-op outside Electron. Idempotent —
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

  if (instance?.ownerDeviceId) {
    const device = await getElectronDeviceInfo();
    if (!device?.ok || device.deviceId !== instance.ownerDeviceId) return;
  }

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
    const refusal = localFs ? readDesktopRefusal(error) : null;
    if (refusal) {
      return { kind: 'requires-desktop', ...refusal };
    }
    throw error;
  }
  await applyPostResyncInstanceRefresh(connectorId, fullSync);
  return { kind: 'backend' };
}

/**
 * Turn sync on. For Local FS in Electron, sends this device's identity so the
 * backend can claim it as owner on first enable. Node refuses a Local FS
 * enable with DESKTOP_OFFLINE, DESKTOP_UNCLAIMED or
 * DESKTOP_OWNED_BY_OTHER_DEVICE, reported as `requires-desktop` instead of
 * thrown. Does not refresh the row — callers do that.
 */
export async function toggleConnectorSyncOn(
  connectorId: string,
  connectorType?: string
): Promise<ResyncOutcome> {
  const localFs = !!connectorType && isLocalFsConnectorType(connectorType);
  let device: { deviceId: string; deviceName: string } | undefined;
  if (localFs) {
    const info = await getElectronDeviceInfo();
    if (info?.ok === false) {
      throw new Error(
        i18n.t('workspace.connectors.localFsDesktop.deviceIdentityError', { error: info.error })
      );
    }
    if (info?.ok) device = { deviceId: info.deviceId, deviceName: info.deviceName };
  }
  if (connectorType) {
    await prepareLocalFsForEnable(connectorId, connectorType);
  }
  try {
    await ConnectorsApi.toggleConnector(connectorId, 'sync', device);
  } catch (error) {
    const refusal = localFs ? readDesktopRefusal(error) : null;
    if (refusal) {
      if (refusal.reason === 'other_device') {
        // prepareLocalFsForEnable may have mounted a watcher before the row showed the owner.
        await stopElectronLocalSync(connectorId).catch((stopError: unknown) => {
          console.warn('[local-sync] could not stop watcher after ownership refusal:', stopError);
        });
      }
      return { kind: 'requires-desktop', ...refusal };
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
