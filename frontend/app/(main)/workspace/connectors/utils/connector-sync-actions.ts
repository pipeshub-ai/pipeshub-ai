import { isElectron } from '@/lib/electron';
import { ConnectorsApi } from '../api';
import { CONNECTOR_INSTANCE_STATUS, LOCAL_FS_DESKTOP_OFFLINE } from '../constants';
import { useConnectorsStore } from '../store';
import type { ConnectorInstance } from '../types';
import { isLocalFsConnectorType } from './local-fs-helpers';
import {
  buildLocalSyncStartOptionsFromConnectorConfig,
  checkLocalRootPathConflict,
  extractLocalFsRootPath,
  startElectronLocalSync,
} from './electron-local-sync';
import { refreshConnectorInstanceDetails } from './refresh-instance-details';

/**
 * Where the resync was performed. Every connector — Local FS included — now
 * goes through the backend: the connector service runs `run_sync` and pulls
 * file events from the desktop over the socket relay, so pressing Sync from a
 * browser works as long as the user's desktop app is running. `requires-desktop`
 * is what the backend reports when it is not.
 */
export type ResyncOutcome =
  | { kind: 'backend' }
  | { kind: 'requires-desktop' };

/**
 * Matched on the explicit code, not on 409 — the resync route already uses 409
 * for "a sync is already running", and treating that as an offline desktop
 * would tell the user the opposite of what happened.
 *
 * The resync HTTP route returns 200 once the job is queued. A desktop that is
 * offline is written to the App node as `lastError: DESKTOP_OFFLINE` after
 * the pull; {@link waitForLocalFsPullOutcome} reads that.
 */
function isDesktopOfflineError(error: unknown): boolean {
  const body = error as { code?: string; details?: { code?: string }; message?: string };
  if (body?.code === 'DESKTOP_OFFLINE' || body?.details?.code === 'DESKTOP_OFFLINE') {
    return true;
  }
  return /DESKTOP_OFFLINE/.test(String(body?.message || ''));
}

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

const LOCAL_FS_OUTCOME_POLL_MS = 400;
const LOCAL_FS_OUTCOME_TIMEOUT_MS = 12_000;
/** If lastError was already DESKTOP_OFFLINE, don't wait the full timeout
 * when this run never flips to SYNCING (skip finishes in ~200ms). */
const STALE_OFFLINE_CONFIRM_MS = 1_200;

function isInProgressStatus(status?: string | null): boolean {
  const normalized = (status ?? CONNECTOR_INSTANCE_STATUS.IDLE).toUpperCase();
  return (
    normalized === CONNECTOR_INSTANCE_STATUS.SYNCING ||
    normalized === CONNECTOR_INSTANCE_STATUS.FULL_SYNCING
  );
}

function isDesktopOfflineLastError(lastError?: string | null): boolean {
  return lastError === LOCAL_FS_DESKTOP_OFFLINE;
}

function readPullOutcome(instance: ConnectorInstance): {
  offline: boolean;
  inProgress: boolean;
} {
  return {
    offline: isDesktopOfflineLastError(instance.lastError),
    inProgress: isInProgressStatus(instance.status),
  };
}

function findStoredConnector(connectorId: string): ConnectorInstance | undefined {
  const state = useConnectorsStore.getState();
  return (
    state.activeConnectors.find((c) => c._key === connectorId) ??
    state.instances.find((c) => c._key === connectorId) ??
    (state.selectedInstance?._key === connectorId ? state.selectedInstance : undefined)
  );
}

export type LocalFsPullBaseline = {
  lastErrorBefore?: string | null;
  updatedAtBefore?: number | null;
};

function isThisRunOffline(
  instance: ConnectorInstance,
  args: { hadStaleOffline: boolean; sawThisRun: boolean; updatedAtBefore?: number | null }
): boolean {
  if (!isDesktopOfflineLastError(instance.lastError)) {
    return false;
  }
  if (!args.hadStaleOffline || args.sawThisRun) {
    return true;
  }
  const updatedAt = instance.updatedAtTimestamp ?? 0;
  return updatedAt > (args.updatedAtBefore ?? 0);
}

/**
 * Resync/toggle return before the pull. Poll status + lastError only — do
 * not refresh config or upsert the store on every tick (that remounts the
 * instance list). Write the store once when the outcome is known.
 *
 * Pass lastError + updatedAt from *before* the action. A leftover
 * DESKTOP_OFFLINE is ignored until this run writes a newer updatedAt,
 * we see SYNCING, or a short idle confirm elapses.
 */
export async function waitForLocalFsPullOutcome(
  connectorId: string,
  options?: LocalFsPullBaseline
): Promise<ResyncOutcome> {
  const hadStaleOffline = isDesktopOfflineLastError(options?.lastErrorBefore);
  const updatedAtBefore = options?.updatedAtBefore ?? 0;
  const startedAt = Date.now();
  const deadline = startedAt + LOCAL_FS_OUTCOME_TIMEOUT_MS;
  let sawThisRun = false;
  let latest: ConnectorInstance | null = null;

  while (Date.now() < deadline) {
    latest = await ConnectorsApi.getConnectorInstance(connectorId);
    const { offline, inProgress } = readPullOutcome(latest);
    const staleConfirmed =
      hadStaleOffline &&
      offline &&
      !inProgress &&
      Date.now() - startedAt >= STALE_OFFLINE_CONFIRM_MS;

    if (inProgress) {
      sawThisRun = true;
    } else if (
      isThisRunOffline(latest, { hadStaleOffline, sawThisRun, updatedAtBefore }) ||
      staleConfirmed
    ) {
      useConnectorsStore.getState().upsertConnectorInstance(latest);
      return { kind: 'requires-desktop' };
    } else if (sawThisRun && !offline) {
      useConnectorsStore.getState().upsertConnectorInstance(latest);
      return { kind: 'backend' };
    }

    await new Promise((resolve) => setTimeout(resolve, LOCAL_FS_OUTCOME_POLL_MS));
  }

  const last = latest ?? (await ConnectorsApi.getConnectorInstance(connectorId));
  useConnectorsStore.getState().upsertConnectorInstance(last);
  if (isDesktopOfflineLastError(last.lastError)) {
    return { kind: 'requires-desktop' };
  }
  return { kind: 'backend' };
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
 * connector on the socket. Toggle-on publishes an immediate pull; if this
 * runs after that publish, Node answers DESKTOP_OFFLINE. No-op outside
 * Electron. Idempotent — `LocalSyncManager.start` returns early for an
 * unchanged config.
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
  if (isLocalFsConnectorType(connectorType)) {
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
    if (isLocalFsConnectorType(connectorType) && isDesktopOfflineError(error)) {
      return { kind: 'requires-desktop' };
    }
    throw error;
  }
  const baseline = findStoredConnector(connectorId);
  await applyPostResyncInstanceRefresh(connectorId, fullSync);
  if (isLocalFsConnectorType(connectorType)) {
    return waitForLocalFsPullOutcome(connectorId, {
      lastErrorBefore: baseline?.lastError,
      updatedAtBefore: baseline?.updatedAtTimestamp,
    });
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
): Promise<ResyncOutcome | null> {
  if (!instance._key) {
    throw new Error('startConnectorSync: connectorId (_key) is required');
  }
  const fresh = await ConnectorsApi.getConnectorInstance(instance._key);
  const type = fresh.type || instance.type;
  if (!fresh.isActive) {
    if (type) {
      await prepareLocalFsForEnable(instance._key, type);
    }
    await ConnectorsApi.toggleConnector(instance._key, 'sync');
    await refreshConnectorInstanceDetails(instance._key);
    if (type && isLocalFsConnectorType(type)) {
      return waitForLocalFsPullOutcome(instance._key, {
        lastErrorBefore: fresh.lastError,
        updatedAtBefore: fresh.updatedAtTimestamp,
      });
    }
    return null;
  }
  if (!type) {
    throw new Error(
      `startConnectorSync: connector type unknown for instance ${instance._key}`
    );
  }
  return runConnectorResync({ connectorId: instance._key, connectorType: type });
}
