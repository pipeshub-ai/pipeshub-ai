import type { AxiosError } from 'axios';
import { isElectron } from '@/lib/electron';
import { isProcessedError } from '@/lib/api/api-error';
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

/** Matches the Node `ConnectorSyncInProgressError` code (HttpError prefixes `HTTP_`). */
const SYNC_IN_PROGRESS_CODE = 'HTTP_CONNECTOR_SYNC_IN_PROGRESS';
/** Matches the Node `ConnectorSyncLockedError` code (full-sync prep / non-forceable). */
const SYNC_LOCKED_CODE = 'HTTP_CONNECTOR_SYNC_LOCKED';
/** Older Node builds threw plain CONFLICT while the connector was locked. */
const LEGACY_CONFLICT_CODE = 'HTTP_CONFLICT';

/**
 * Thrown by {@link runConnectorResync} when the backend rejects the trigger
 * because a sync is already running and `force` was not set. Callers catch this
 * to prompt "cancel current & restart" instead of surfacing a generic error.
 */
export class ConnectorSyncInProgressError extends Error {
  readonly code = 'CONNECTOR_SYNC_IN_PROGRESS' as const;

  constructor() {
    super('A sync is already in progress for this connector.');
    this.name = 'ConnectorSyncInProgressError';
  }
}

/**
 * Thrown when resync is blocked by `isLocked` (full-sync prep). Restarting is
 * not safe — callers should ask the user to wait, not offer force-restart.
 */
export class ConnectorSyncLockedError extends Error {
  readonly code = 'CONNECTOR_SYNC_LOCKED' as const;

  constructor(message?: string) {
    super(
      message ||
        'A sync operation is preparing and cannot be interrupted. Please wait and try again.'
    );
    this.name = 'ConnectorSyncLockedError';
  }
}

export function isConnectorSyncInProgressError(
  err: unknown
): err is ConnectorSyncInProgressError {
  return (
    err instanceof ConnectorSyncInProgressError ||
    (typeof err === 'object' &&
      err !== null &&
      (err as { name?: string; code?: string }).name ===
        'ConnectorSyncInProgressError') ||
    (typeof err === 'object' &&
      err !== null &&
      (err as { code?: string }).code === 'CONNECTOR_SYNC_IN_PROGRESS')
  );
}

export function isConnectorSyncLockedError(
  err: unknown
): err is ConnectorSyncLockedError {
  return (
    err instanceof ConnectorSyncLockedError ||
    (typeof err === 'object' &&
      err !== null &&
      (err as { name?: string; code?: string }).name ===
        'ConnectorSyncLockedError') ||
    (typeof err === 'object' &&
      err !== null &&
      (err as { code?: string }).code === 'CONNECTOR_SYNC_LOCKED')
  );
}

/** Read the nested `error.code` off a reshaped API error's original Axios error. */
function apiErrorCode(err: unknown): string | undefined {
  if (!isProcessedError(err) || err.statusCode !== 409) return undefined;
  const axiosErr = err.originalError as
    | AxiosError<{ error?: { code?: string } | string }>
    | undefined;
  const nested = axiosErr?.response?.data?.error;
  if (typeof nested === 'string') return undefined;
  const code = nested?.code;
  return typeof code === 'string' ? code : undefined;
}

/** Exported for unit tests — maps a 409 resync rejection to UI handling. */
export function classifyResyncConflict(
  err: unknown
): 'restartable' | 'locked' | null {
  if (!isProcessedError(err) || err.statusCode !== 409) return null;
  const code = apiErrorCode(err);
  if (code === SYNC_IN_PROGRESS_CODE) return 'restartable';
  if (code === SYNC_LOCKED_CODE) return 'locked';
  // Pre-fix Node gate: locked full-sync prep returned plain HTTP_CONFLICT.
  if (code === LEGACY_CONFLICT_CODE) return 'locked';
  // Missing/unknown code but clearly a sync-busy 409 — prefer the restart dialog.
  if (/sync.*(in progress|already)|full sync/i.test(err.message)) {
    return 'restartable';
  }
  return null;
}

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
  /** Cancel any in-flight sync and restart (skips the backend's in-progress guard). */
  force?: boolean;
}): Promise<ResyncOutcome> {
  const { connectorId, connectorType, fullSync = false, force = false } = args;
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
    await ConnectorsApi.resyncConnector(connectorId, connectorType, fullSync, force);
  } catch (err) {
    const refusal = localFs ? readDesktopRefusal(err) : null;
    if (refusal) {
      return { kind: 'requires-desktop', ...refusal };
    }
    const conflict = classifyResyncConflict(err);
    if (conflict === 'restartable') {
      throw new ConnectorSyncInProgressError();
    }
    if (conflict === 'locked') {
      throw new ConnectorSyncLockedError(
        isProcessedError(err) ? err.message : undefined
      );
    }
    throw err;
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
function persistConnectorActive(connectorId: string, isActive: boolean): void {
  const state = useConnectorsStore.getState();
  const existing =
    state.activeConnectors.find((c) => c._key === connectorId) ??
    state.instances.find((c) => c._key === connectorId) ??
    (state.selectedInstance?._key === connectorId ? state.selectedInstance : undefined);
  if (!existing) return;
  state.upsertConnectorInstance({
    ...existing,
    isActive,
  } as ConnectorInstance);
}

export async function startConnectorSync(
  instance: { _key: string } & Partial<Pick<ConnectorInstance, 'type'>>,
  options: { force?: boolean } = {}
): Promise<ResyncOutcome> {
  if (!instance._key) {
    throw new Error('startConnectorSync: connectorId (_key) is required');
  }
  const fresh = await ConnectorsApi.getConnectorInstance(instance._key);
  const type = fresh.type || instance.type;
  if (!fresh.isActive) {
    // Flip the card to "sync enabled" immediately; reconcile from GET after toggle.
    persistConnectorActive(instance._key, true);
    let outcome: ResyncOutcome;
    try {
      outcome = await toggleConnectorSyncOn(instance._key, type);
    } catch (err) {
      persistConnectorActive(instance._key, false);
      throw err;
    }
    if (outcome.kind === 'backend') {
      await refreshConnectorInstanceDetails(instance._key);
    } else {
      persistConnectorActive(instance._key, false);
    }
    return outcome;
  }
  if (!type) {
    throw new Error(
      `startConnectorSync: connector type unknown for instance ${instance._key}`
    );
  }
  return runConnectorResync({
    connectorId: instance._key,
    connectorType: type,
    force: options.force ?? false,
  });
}
