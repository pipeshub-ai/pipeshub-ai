import { STRATEGY_LABELS, INTERVAL_LABELS, CONNECTOR_INSTANCE_STATUS } from '../../constants';
import { isConnectorInstanceOAuthAuthIncompleteForSyncUi } from '../../utils/auth-helpers';
import type {
  ConnectorInstance,
  ConnectorConfig,
  ConnectorStatsResponse,
  FilterSchemaField,
  InstanceSyncStatus,
} from '../../types';

// ========================================
// Config-derived helpers
// ========================================

export function getSyncStrategyLabel(config?: ConnectorConfig): string | null {
  if (!config?.config?.sync?.selectedStrategy) return null;
  const strategy = config.config.sync.selectedStrategy;
  return (
    STRATEGY_LABELS[strategy] ??
    strategy.charAt(0).toUpperCase() + strategy.slice(1).toLowerCase()
  );
}

export function getSyncIntervalLabel(config?: ConnectorConfig): string | null {
  if (!config?.config?.sync?.selectedStrategy) return null;
  if (config.config.sync.selectedStrategy !== 'SCHEDULED') return null;
  const minutes = config.config.sync.scheduledConfig?.intervalMinutes;
  if (!minutes) return null;
  return INTERVAL_LABELS[minutes] ?? `Every ${minutes} min`;
}

export function getRecordsSelectedInfo(
  config?: ConnectorConfig,
): { label: string; count: number } | null {
  if (!config?.config?.filters?.sync) return null;

  const syncFilters = config.config.filters.sync;
  const fields = syncFilters.schema?.fields ?? syncFilters.customFields ?? [];
  const values = syncFilters.values ?? syncFilters.customValues ?? {};

  for (const field of fields as FilterSchemaField[]) {
    const fieldValue = values[field.name];
    if (Array.isArray(fieldValue) && fieldValue.length > 0) {
      const label = (field.displayName ?? field.name).toUpperCase() + ' SELECTED';
      return { label, count: fieldValue.length };
    }
  }

  return null;
}

// ========================================
// Stats-derived helpers
// ========================================

export function getTotalRecords(
  stats?: ConnectorStatsResponse['data'],
): number | null {
  if (!stats?.stats) return null;
  return stats.stats.total;
}

export function getSyncedRecords(
  stats?: ConnectorStatsResponse['data'],
): number | null {
  if (!stats?.stats?.indexingStatus) return null;
  return stats.stats.indexingStatus.COMPLETED ?? 0;
}

export function getFailedRecords(
  stats?: ConnectorStatsResponse['data'],
): number | null {
  if (!stats?.stats?.indexingStatus) return null;
  return stats.stats.indexingStatus.FAILED ?? 0;
}

export function getUnsupportedRecords(
  stats?: ConnectorStatsResponse['data'],
): number | null {
  if (!stats?.stats?.indexingStatus) return null;
  return stats.stats.indexingStatus.FILE_TYPE_NOT_SUPPORTED ?? 0;
}

// ========================================
// Status derivation
// ========================================

/**
 * Derive the effective sync status from instance + stats.
 *
 * Priority order:
 * 1. Hard backend status field (DELETING, SYNCING) always wins.
 * 2. Instance-level boolean state (isActive; OAuth-only auth completion vs isConfigured).
 * 3. Stats-derived state (in-progress, completed, failed counts).
 */
export function deriveSyncStatus(
  instance: ConnectorInstance,
  stats?: ConnectorStatsResponse['data'],
  connectorConfig?: ConnectorConfig,
): InstanceSyncStatus {
  // 1. Backend-set hard states
  if (instance.status === CONNECTOR_INSTANCE_STATUS.DELETING) return 'sync_disabled';
  if (instance.status === CONNECTOR_INSTANCE_STATUS.SYNCING) return 'syncing';

  // 2. Instance-level boolean state
  if (!instance.isActive) return 'sync_disabled';

  if (isConnectorInstanceOAuthAuthIncompleteForSyncUi(connectorConfig, instance)) {
    return 'auth_incomplete';
  }

  // 3. Derive from stats
  if (!stats?.stats?.indexingStatus) return 'ready_to_sync';

  const idx = stats.stats.indexingStatus;
  const inProgress = (idx.IN_PROGRESS ?? 0) + (idx.QUEUED ?? 0);
  const completed  = idx.COMPLETED ?? 0;
  const failed     = idx.FAILED ?? 0;

  if (inProgress > 0) return 'syncing';

  // sync_failed: nothing completed yet, only failures
  if (failed > 0 && completed === 0) return 'sync_failed';

  // sync_complete: at least some records completed (error count shown separately in pill)
  if (completed > 0 || failed > 0) return 'sync_complete';

  return 'ready_to_sync';
}
