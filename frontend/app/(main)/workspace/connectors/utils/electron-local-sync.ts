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
  });
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
