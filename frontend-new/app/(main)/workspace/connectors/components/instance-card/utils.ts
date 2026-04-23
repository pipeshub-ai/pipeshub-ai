import { STRATEGY_LABELS, INTERVAL_LABELS, CONNECTOR_INSTANCE_STATUS } from '../../constants';
import { isConnectorInstanceOAuthAuthIncompleteForSyncUi, isOAuthType } from '../../utils/auth-helpers';
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
 * Derive sync pill status plus the OAuth-only "authenticate first" flag (single evaluation for card UI).
 *
 * Priority order for `status`:
 * 1. Hard backend status field (DELETING, SYNCING) always wins.
 * 2. Instance-level state: `isActive`, plus OAuth-only auth completion
 *    (via {@link isConnectorInstanceOAuthAuthIncompleteForSyncUi}).
 * 3. Stats-derived state (in-progress, completed, failed counts).
 */
export function deriveSyncStatusState(
  instance: ConnectorInstance,
  stats?: ConnectorStatsResponse['data'],
  connectorConfig?: ConnectorConfig,
): { status: InstanceSyncStatus; oauthAuthIncompleteForSync: boolean } {
  // Legacy guard: Gmail/Drive Workspace team connectors were migrated from OAUTH→CUSTOM.
  // Old DB rows still carry authType:"OAUTH" but there is no OAuth consent flow anymore,
  // so they must never be flagged as auth-incomplete for sync.
  const LEGACY_TEAM_WORKSPACE_TYPES = ['Gmail Workspace', 'Drive Workspace'];
  const isLegacyWorkspaceOAuth =
    LEGACY_TEAM_WORKSPACE_TYPES.includes(instance.type) &&
    instance.scope === 'team' &&
    isOAuthType(instance.authType ?? connectorConfig?.authType ?? '');

  const oauthAuthIncompleteForSync = isLegacyWorkspaceOAuth
    ? false
    : isConnectorInstanceOAuthAuthIncompleteForSyncUi(connectorConfig, instance);

  // 1. Backend-set hard states
  if (instance.status === CONNECTOR_INSTANCE_STATUS.DELETING) {
    return { status: 'sync_disabled', oauthAuthIncompleteForSync };
  }
  if (instance.status === CONNECTOR_INSTANCE_STATUS.SYNCING) {
    return { status: 'syncing', oauthAuthIncompleteForSync };
  }

  // 2. Instance-level boolean state
  if (!instance.isActive) {
    return { status: 'sync_disabled', oauthAuthIncompleteForSync };
  }

  if (oauthAuthIncompleteForSync) {
    return { status: 'auth_incomplete', oauthAuthIncompleteForSync };
  }

  // 3. Derive from stats
  if (!stats?.stats?.indexingStatus) {
    return { status: 'ready_to_sync', oauthAuthIncompleteForSync };
  }

  const idx = stats.stats.indexingStatus;
  const inProgress = (idx.IN_PROGRESS ?? 0) + (idx.QUEUED ?? 0);
  const completed  = idx.COMPLETED ?? 0;
  const failed     = idx.FAILED ?? 0;

  if (inProgress > 0) {
    return { status: 'syncing', oauthAuthIncompleteForSync };
  }

  // sync_failed: nothing completed yet, only failures
  if (failed > 0 && completed === 0) {
    return { status: 'sync_failed', oauthAuthIncompleteForSync };
  }

  // sync_complete: at least some records completed (error count shown separately in pill)
  if (completed > 0 || failed > 0) {
    return { status: 'sync_complete', oauthAuthIncompleteForSync };
  }

  return { status: 'ready_to_sync', oauthAuthIncompleteForSync };
}

/** @see deriveSyncStatusState */
export function deriveSyncStatus(
  instance: ConnectorInstance,
  stats?: ConnectorStatsResponse['data'],
  connectorConfig?: ConnectorConfig,
): InstanceSyncStatus {
  return deriveSyncStatusState(instance, stats, connectorConfig).status;
}
