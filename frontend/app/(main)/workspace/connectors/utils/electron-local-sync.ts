import { getApiBaseUrl, isElectron } from '@/lib/utils/api-base-url';
import type { ConnectorConfig, LocalSyncStatus } from '../types';

export interface LocalSyncScheduledConfigPayload {
  intervalMinutes: number;
  startTime?: number;
  timezone?: string;
}

interface LocalSyncStartPayload {
  connectorId: string;
  connectorName: string;
  rootPath: string;
  accessToken: string;
  /** Crawling-manager connector segment (usually the connector `type` string). */
  connectorDisplayType?: string;
  syncStrategy?: 'MANUAL' | 'SCHEDULED';
  scheduledConfig?: LocalSyncScheduledConfigPayload;
  /** Mirrors connector sync custom field `include_subfolders` (default true if omitted). */
  includeSubfolders?: boolean;
  /** From sync filter `file_extensions`; omit to sync all extensions (matches watcher). */
  allowedExtensions?: string[];
}

export type LocalFsWatcherOptionsPayload = Pick<
  LocalSyncStartPayload,
  'includeSubfolders' | 'allowedExtensions'
>;

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

/** Filter may be a raw list or a `{ value: ... }` envelope from the API. */
function normalizeFileExtensionsRaw(raw: unknown): string[] | undefined {
  let v: unknown = raw;
  if (v !== null && typeof v === 'object' && !Array.isArray(v) && 'value' in v) {
    v = (v as { value?: unknown }).value;
  }
  if (v == null) return undefined;
  const list = Array.isArray(v)
    ? v
    : typeof v === 'string'
      ? v.split(/[,\s]+/)
      : [];
  const allowed = list
    .map((x) => String(x).trim().toLowerCase().replace(/^\./, ''))
    .filter(Boolean);
  return allowed.length > 0 ? allowed : undefined;
}

interface ElectronLocalSyncApi {
  start: (payload: {
    connectorId: string;
    connectorName: string;
    rootPath: string;
    apiBaseUrl: string;
    accessToken: string;
    connectorDisplayType?: string;
    syncStrategy?: 'MANUAL' | 'SCHEDULED';
    scheduledConfig?: LocalSyncScheduledConfigPayload;
    includeSubfolders?: boolean;
    allowedExtensions?: string[];
  }) => Promise<LocalSyncStatus>;
  stop: (connectorId: string) => Promise<LocalSyncStatus>;
  status: (connectorId: string) => Promise<LocalSyncStatus>;
  replay: (connectorId: string) => Promise<LocalSyncStatus>;
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
    accessToken: payload.accessToken,
    ...(payload.connectorDisplayType
      ? { connectorDisplayType: payload.connectorDisplayType }
      : {}),
    ...(payload.syncStrategy ? { syncStrategy: payload.syncStrategy } : {}),
    ...(payload.scheduledConfig ? { scheduledConfig: payload.scheduledConfig } : {}),
    ...(payload.includeSubfolders !== undefined ? { includeSubfolders: payload.includeSubfolders } : {}),
    ...(payload.allowedExtensions && payload.allowedExtensions.length > 0
      ? { allowedExtensions: payload.allowedExtensions }
      : {}),
  });
}

/**
 * Maps Local FS connector saved settings into watcher/full-sync options so the
 * Electron app matches backend indexing rules (subfolders + extension filter).
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

  const syncFilterBlock = config.config.filters?.sync;
  const values = syncFilterBlock && typeof syncFilterBlock === 'object' && 'values' in syncFilterBlock
    ? (syncFilterBlock as { values?: Record<string, unknown> }).values
    : undefined;
  const rawExt =
    values?.file_extensions ??
    (syncFilterBlock as Record<string, unknown> | undefined)?.file_extensions;

  const ext = normalizeFileExtensionsRaw(rawExt);
  if (ext) out.allowedExtensions = ext;

  return out;
}

/**
 * Maps saved connector sync settings into the payload Electron's local-sync
 * manager needs to run scheduled ticks (replay + disk rescan). Without this,
 * the desktop watcher stays MANUAL-only while the backend still runs crawl
 * jobs — scheduled rescans never pull offline / missed file changes.
 */
export function buildLocalSyncScheduleFromConnectorConfig(
  config: ConnectorConfig | null | undefined,
  connectorDisplayType?: string | null
): Pick<
  LocalSyncStartPayload,
  'syncStrategy' | 'scheduledConfig' | 'connectorDisplayType'
> {
  const out: Pick<
    LocalSyncStartPayload,
    'syncStrategy' | 'scheduledConfig' | 'connectorDisplayType'
  > = {};
  const typeTrim = typeof connectorDisplayType === 'string' ? connectorDisplayType.trim() : '';
  if (typeTrim) out.connectorDisplayType = typeTrim;

  const selected = config?.config?.sync?.selectedStrategy;
  const sched = config?.config?.sync?.scheduledConfig;
  if (selected !== 'SCHEDULED' || !sched) return out;

  const intervalMinutes = Math.max(1, Number(sched.intervalMinutes) || 60);
  let startTime: number | undefined;
  if (sched.startDateTime) {
    const ms = Date.parse(String(sched.startDateTime));
    if (Number.isFinite(ms)) startTime = ms;
  } else if (typeof sched.startTime === 'number' && Number.isFinite(sched.startTime)) {
    startTime = sched.startTime;
  }

  out.syncStrategy = 'SCHEDULED';
  out.scheduledConfig = {
    intervalMinutes,
    ...(sched.timezone ? { timezone: String(sched.timezone) } : { timezone: 'UTC' }),
    ...(startTime !== undefined ? { startTime } : {}),
  };
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

export async function replayElectronLocalSync(connectorId: string): Promise<LocalSyncStatus | null> {
  const api = getElectronLocalSyncApi();
  if (!api) return null;
  return api.replay(connectorId);
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
