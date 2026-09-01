import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import { isElectron } from '@/lib/electron';
import { useAuthStore } from '@/config';
import { ConnectorsApi } from '../api';
import { isLocalFsConnectorType } from './local-fs-helpers';
import type { ConnectorConfig, LocalSyncStatus } from '../types';

interface LocalSyncStartPayload {
  connectorId: string;
  connectorName: string;
  rootPath: string;
  /** Crawling-manager connector segment (usually the connector `type` string). */
  connectorDisplayType?: string;
  syncStrategy?: 'MANUAL' | 'SCHEDULED';
  /** Mirrors connector sync custom field `include_subfolders` (default true if omitted). */
  includeSubfolders?: boolean;
}

export type LocalFsWatcherOptionsPayload = Pick<LocalSyncStartPayload, 'includeSubfolders'>;

/** API may send booleans as strings (e.g. saved JSON). */
function parseIncludeSubfolders(merged: Record<string, unknown>): boolean | undefined {
  const v = merged.include_subfolders;
  if (v === undefined || v === null) return undefined;
  if (typeof v === 'boolean') return v;
  if (typeof v === 'string') {
    const s = v.trim().toLowerCase();
    if (s === 'true' || s === '1') return true;
    if (s === 'false' || s === '0') return false;
  }
  if (typeof v === 'number' && (v === 0 || v === 1)) return v === 1;
  return undefined;
}

export interface LocalRootPathConflictResult {
  available: boolean;
  ownerConnectorId?: string;
  ownerConnectorName?: string;
}

interface ElectronLocalSyncApi {
  start: (payload: {
    connectorId: string;
    connectorName: string;
    rootPath: string;
    apiBaseUrl: string;
    connectorDisplayType?: string;
    syncStrategy?: 'MANUAL' | 'SCHEDULED';
    includeSubfolders?: boolean;
  }) => Promise<LocalSyncStatus>;
  checkRootPathConflict: (
    connectorId: string,
    rootPath: string
  ) => Promise<LocalRootPathConflictResult>;
  stop: (connectorId: string) => Promise<LocalSyncStatus>;
  remove: (connectorId: string) => Promise<{ ok: boolean }>;
  reap: (connectorIds: string[]) => Promise<{ removed: string[] }>;
  status: (connectorId: string) => Promise<LocalSyncStatus>;
  bootstrap: () => Promise<Array<{ connectorId: string; ok: boolean; error?: string }>>;
  setCredentials: (
    refreshToken: string,
    apiBaseUrl: string
  ) => Promise<{ ok: boolean; persisted?: boolean; reason?: string; error?: string }>;
  clearCredentials: () => Promise<{ ok: boolean }>;
}

function getElectronLocalSyncApi() {
  if (!isElectron()) return null;
  const api = (window as unknown as { electronAPI?: { localSync?: ElectronLocalSyncApi } })
    .electronAPI?.localSync;
  if (!api) return null;
  return api;
}

export async function startElectronLocalSync(
  payload: LocalSyncStartPayload
): Promise<LocalSyncStatus | null> {
  const api = getElectronLocalSyncApi();
  if (!api) return null;

  const apiBaseUrl = getApiBaseUrl();
  if (!apiBaseUrl) return null;

  return api.start({
    connectorId: payload.connectorId,
    connectorName: payload.connectorName,
    rootPath: payload.rootPath,
    apiBaseUrl,
    ...(payload.connectorDisplayType
      ? { connectorDisplayType: payload.connectorDisplayType }
      : {}),
    ...(payload.syncStrategy ? { syncStrategy: payload.syncStrategy } : {}),
    ...(payload.includeSubfolders !== undefined ? { includeSubfolders: payload.includeSubfolders } : {}),
  });
}

/**
 * Hand the refresh token to the Electron main process at sign-in.
 *
 * Main persists it with `safeStorage` and mints its own access tokens, so the
 * desktop keeps answering the server's pull with the window closed — which is
 * the entire point of moving sync cadence server-side.
 */
export async function setElectronDesktopCredentials(): Promise<void> {
  const api = getElectronLocalSyncApi();
  if (!api) return;

  const apiBaseUrl = getApiBaseUrl();
  const refreshToken = useAuthStore.getState().refreshToken;
  if (!apiBaseUrl || !refreshToken) return;

  const result = await api.setCredentials(refreshToken, apiBaseUrl);
  if (result?.ok && result.persisted === false) {
    console.warn(`[local-sync] ${result.reason}`);
  }
}

export async function clearElectronDesktopCredentials(): Promise<void> {
  const api = getElectronLocalSyncApi();
  if (!api) return;
  await api.clearCredentials();
}

/**
 * Read-only preflight: does another Local FS connector already watch this
 * root? Call this *before* activating (toggling sync on / creating) a Local
 * FS connector so a conflicting path never flips the backend to active in
 * the first place — the watcher-start failure that follows an already-active
 * connector is confusing and leaves the instance stuck active with no
 * watcher. Returns `available: true` outside Electron (nothing to conflict
 * with client-side) so callers can always await this unconditionally.
 */
export async function checkLocalRootPathConflict(
  connectorId: string,
  rootPath: string
): Promise<LocalRootPathConflictResult> {
  const api = getElectronLocalSyncApi();
  if (!api) return { available: true };
  return api.checkRootPathConflict(connectorId, rootPath);
}

/**
 * Maps Local FS connector saved settings into watcher/full-sync options so the
 * Electron app matches backend indexing rules.
 */
export function buildLocalFsWatcherOptionsFromConnectorConfig(
  config: ConnectorConfig | null | undefined
): LocalFsWatcherOptionsPayload {
  const out: LocalFsWatcherOptionsPayload = {};
  if (!config?.config) return out;

  const sync = config.config.sync;
  if (sync) {
    const merged: Record<string, unknown> = {
      ...(sync.values || {}),
      ...(sync.customValues || {}),
    };
    const inc = parseIncludeSubfolders(merged);
    if (inc !== undefined) out.includeSubfolders = inc;
  }

  return out;
}

/**
 * Reads only what the desktop watcher needs from the saved connector config.
 * Sync cadence is no longer a desktop concern — the connector service schedules
 * every run and pulls, so the watcher's only job is keeping the journal warm.
 */
export function buildLocalSyncStartOptionsFromConnectorConfig(
  config: ConnectorConfig | null | undefined,
  connectorDisplayType?: string | null
): Pick<LocalSyncStartPayload, 'syncStrategy' | 'connectorDisplayType' | 'includeSubfolders'> {
  const out: Pick<
    LocalSyncStartPayload,
    'syncStrategy' | 'connectorDisplayType' | 'includeSubfolders'
  > = { ...buildLocalFsWatcherOptionsFromConnectorConfig(config) };
  const typeTrim = typeof connectorDisplayType === 'string' ? connectorDisplayType.trim() : '';
  if (typeTrim) out.connectorDisplayType = typeTrim;
  // Informational only: surfaced in the desktop status card so the UI can say
  // which cadence the server is running.
  if (config?.config?.sync?.selectedStrategy === 'SCHEDULED') out.syncStrategy = 'SCHEDULED';
  return out;
}

export async function stopElectronLocalSync(connectorId: string): Promise<LocalSyncStatus | null> {
  const api = getElectronLocalSyncApi();
  if (!api) return null;
  return api.stop(connectorId);
}

export async function getElectronLocalSyncStatus(
  connectorId: string
): Promise<LocalSyncStatus | null> {
  const api = getElectronLocalSyncApi();
  if (!api) return null;
  return api.status(connectorId);
}

/**
 * Call when a connector is **deleted**, not merely deactivated or navigated
 * away from. Unmounting alone leaves the journal meta on disk, and the boot
 * bootstrap mounts a watcher for every connector the journal knows about — so
 * the deleted connector comes back on the next launch and holds its sync root
 * against any new connector pointed at the same folder.
 */
export async function removeElectronLocalSync(connectorId: string): Promise<void> {
  const api = getElectronLocalSyncApi();
  if (!api) return;
  await api.remove(connectorId);
}

/**
 * Boot-time mount for **every** active Local FS connector, not just scheduled
 * ones. The server drives all sync now, and a pull that lands on a machine
 * with no watcher mounted finds an empty journal — the responder can mount one
 * lazily, but that turns every incremental sync into a full rescan. Mounting
 * at boot keeps the journal warm so an incremental pull is cheap.
 *
 * Enumerates from the backend rather than the Electron journal so instances
 * that were never opened on this machine are covered too. Throws if the
 * connector list can't be fetched, so the caller can fall back to
 * `bootstrapElectronLocalSyncFromJournal`; a per-instance failure only skips
 * that instance.
 */
export async function startLocalWatchers(): Promise<void> {
  const api = getElectronLocalSyncApi();
  if (!api) return;

  const { connectors } = await ConnectorsApi.getActiveConnectors('personal');
  const localFsConnectors = (connectors || []).filter(
    (connector) => Boolean(connector._key) && isLocalFsConnectorType(connector.type)
  );

  // Reap first, and against *every* Local FS instance the backend knows about
  // rather than the eligible subset below — a connector that is merely toggled
  // off still exists, and dropping its journal would throw away pending events.
  // This is the only place with an authoritative view of what still exists, so
  // it is what undoes a delete that happened while the desktop was closed.
  await api.reap(localFsConnectors.map((connector) => connector._key as string));

  const eligible = localFsConnectors.filter(
    (connector) => connector.isActive && connector.isConfigured && connector.isAuthenticated
  );

  await Promise.allSettled(
    eligible.map(async (connector) => {
      const connectorId = connector._key as string;
      const config = await ConnectorsApi.getConnectorConfig(connectorId);
      const rootPath = extractLocalFsRootPath(config);
      if (!rootPath) return;

      await startElectronLocalSync({
        connectorId,
        connectorName: connector.name,
        rootPath,
        ...buildLocalSyncStartOptionsFromConnectorConfig(config, connector.type),
      });
    })
  );
}

/**
 * Offline fallback for `startLocalWatchers`: brings up the Local FS connectors
 * the Electron journal already knows about (persisted by a prior `start()`)
 * when the backend can't be reached. Connectors already running are skipped,
 * and each start goes through the normal reconcile-or-seed path.
 */
export async function bootstrapElectronLocalSyncFromJournal(): Promise<void> {
  const api = getElectronLocalSyncApi();
  if (!api) return;
  await api.bootstrap();
}

export function extractLocalFsRootPath(
  connectorConfig?: {
    config?: {
      sync?: {
        values?: Record<string, unknown>;
        customValues?: Record<string, unknown>;
      };
    };
  } | null
): string | null {
  const syncConfig = connectorConfig?.config?.sync || {};
  const values = {
    ...(syncConfig.values || {}),
    ...(syncConfig.customValues || {}),
  };

  const preferredKeys = [
    'sync_root_path',
    'rootPath',
    'folderPath',
    'directoryPath',
    'path',
  ];
  for (const key of preferredKeys) {
    const candidate = values[key];
    if (typeof candidate === 'string' && candidate.trim()) {
      return candidate.trim();
    }
  }

  for (const [key, value] of Object.entries(values)) {
    if (
      typeof value === 'string' &&
      value.trim() &&
      /(folder|directory|root|path)/i.test(key)
    ) {
      return value.trim();
    }
  }

  return null;
}
