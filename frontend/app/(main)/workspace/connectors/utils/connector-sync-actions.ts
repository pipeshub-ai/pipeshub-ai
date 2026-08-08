import type { AxiosError } from 'axios';
import { isElectron } from '@/lib/electron';
import { isProcessedError } from '@/lib/api/api-error';
import { ConnectorsApi } from '../api';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import { useConnectorsStore } from '../store';
import type { ConnectorInstance } from '../types';
import { isLocalFsConnectorType } from './local-fs-helpers';
import { fullResyncElectronLocalSync } from './electron-local-sync';
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

export async function runConnectorResync(args: {
  connectorId: string;
  connectorType: string;
  fullSync?: boolean;
  /** Cancel any in-flight sync and restart (skips the backend's in-progress guard). */
  force?: boolean;
}): Promise<ResyncOutcome> {
  const { connectorId, connectorType, fullSync = false, force = false } = args;
  if (isLocalFsConnectorType(connectorType)) {
    if (!isElectron()) {
      return { kind: 'requires-desktop' };
    }
    await fullResyncElectronLocalSync(connectorId);
    await applyPostResyncInstanceRefresh(connectorId, true);
    return { kind: 'electron-local' };
  }
  try {
    await ConnectorsApi.resyncConnector(connectorId, connectorType, fullSync, force);
  } catch (err) {
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
): Promise<ResyncOutcome | null> {
  if (!instance._key) {
    throw new Error('startConnectorSync: connectorId (_key) is required');
  }
  const fresh = await ConnectorsApi.getConnectorInstance(instance._key);
  if (!fresh.isActive) {
    // Flip the card to "sync enabled" immediately; reconcile from GET after toggle.
    persistConnectorActive(instance._key, true);
    try {
      await ConnectorsApi.toggleConnector(instance._key, 'sync');
    } catch (err) {
      persistConnectorActive(instance._key, false);
      throw err;
    }
    await refreshConnectorInstanceDetails(instance._key);
    return null;
  }
  const type = fresh.type || instance.type;
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
