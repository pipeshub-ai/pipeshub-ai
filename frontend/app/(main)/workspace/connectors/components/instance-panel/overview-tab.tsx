'use client';

import React, { useState, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { Flex, Text, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  OverviewStatsGridShimmer,
  OverviewRecordTypesShimmer,
  OverviewTypesBadgeShimmer,
} from './overview-stats-shimmer';
import { useConnectorsStore } from '../../store';
import { ConnectorsApi } from '../../api';
import { fetchInstanceStats } from '../../utils/fetch-instance-stats';
import { useConnectorSyncProgress } from '../../utils/use-connector-sync-progress';
import { ConnectorSyncProgress, describeSyncProgress } from '../connector-sync-progress';
import { useToastStore } from '@/lib/store/toast-store';
import { deriveSyncStatus } from '../instance-card/utils';
import { runConnectorResync } from '../../utils/connector-sync-actions';
import { isElectron } from '@/lib/electron';
import { isLocalFsConnectorType } from '../../utils/local-fs-helpers';
import {
  extractLocalFsRootPath,
  buildLocalSyncScheduleFromConnectorConfig,
  buildLocalFsWatcherOptionsFromConnectorConfig,
  startElectronLocalSync,
  getElectronLocalSyncStatus,
} from '../../utils/electron-local-sync';
import type { IndexingStatus } from '@/app/(main)/knowledge-base/types';
import type {
  ConnectorInstance,
  ConnectorConfig,
  ConnectorStatsResponse,
  RecordsStatus,
  LocalSyncStatus,
} from '../../types';
import { formatSnakeCaseTitle } from '@/lib/utils/formatters';

// ========================================
// Props
// ========================================

interface OverviewTabProps {
  instance: ConnectorInstance;
  /** Stats data from GET /connectors/{connectorId}/stats */
  stats?: ConnectorStatsResponse['data'] | null;
  /** Initial stats fetch in progress (panel open) */
  statsLoading?: boolean;
  /** GET …/config — used to resolve auth type for OAuth-only UI rules */
  connectorConfig?: ConnectorConfig;
  /** Local sync runtime status from Electron watcher manager */
  localSyncStatus?: LocalSyncStatus;
}

// ========================================
// Helpers
// ========================================

function deriveRecordsStatus(
  stats?: ConnectorStatsResponse['data'] | null
): RecordsStatus {
  if (!stats?.stats) {
    return {
      total: 0,
      completed: 0,
      failed: 0,
      unsupported: 0,
      inProgress: 0,
      notStarted: 0,
      autoIndexOff: 0,
      queued: 0,
      empty: 0,
    };
  }

  const idx = stats.stats.indexingStatus;
  return {
    total: stats.stats.total,
    completed: idx.COMPLETED ?? 0,
    failed: idx.FAILED ?? 0,
    unsupported: idx.FILE_TYPE_NOT_SUPPORTED ?? 0,
    inProgress: idx.IN_PROGRESS ?? 0,
    notStarted: idx.NOT_STARTED ?? 0,
    autoIndexOff: idx.AUTO_INDEX_OFF ?? 0,
    queued: idx.QUEUED ?? 0,
    empty: idx.EMPTY ?? 0,
  };
}

function SyncBreakdownItem({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: number;
  tone?: 'default' | 'amber';
}) {
  return (
    <Flex direction="column" gap="1" style={{ minWidth: 64 }}>
      <Text size="1" weight="medium" style={{ color: 'var(--gray-10)', textTransform: 'uppercase', letterSpacing: '0.04px' }}>
        {label}
      </Text>
      <Text
        size="3"
        weight="medium"
        style={{ color: tone === 'amber' ? 'var(--amber-11)' : 'var(--gray-12)' }}
      >
        {value}
      </Text>
    </Flex>
  );
}

// ========================================
// OverviewTab
// ========================================

export function OverviewTab({
  instance,
  stats,
  statsLoading = false,
  connectorConfig,
  localSyncStatus,
}: OverviewTabProps) {
  const { t } = useTranslation();
  const router = useRouter();
  const closeInstancePanel = useConnectorsStore((s) => s.closeInstancePanel);
  const instanceConfigs = useConnectorsStore((s) => s.instanceConfigs);
  const setLocalSyncStatus = useConnectorsStore((s) => s.setLocalSyncStatus);
  const addToast = useToastStore((s) => s.addToast);
  const bumpCatalogRefresh = useConnectorsStore((s) => s.bumpCatalogRefresh);
  const [isRefreshStatsBusy, setIsRefreshStatsBusy] = useState(false);
  const [isHeaderSyncBusy, setIsHeaderSyncBusy] = useState(false);
  const [isReindexFailedBusy, setIsReindexFailedBusy] = useState(false);
  const [isManualIndexBusy, setIsManualIndexBusy] = useState(false);
  const showStatsShimmer = statsLoading || isRefreshStatsBusy;
  const reindexActionsBusy = isReindexFailedBusy || isManualIndexBusy;
  const recordsStatus = useMemo(
    () => (showStatsShimmer ? null : deriveRecordsStatus(stats)),
    [stats, showStatsShimmer]
  );

  // Derive indexed records from byRecordType data
  const byRecordType = stats?.byRecordType ?? [];

  const configForDerive =
    connectorConfig ?? (instance._key ? instanceConfigs[instance._key] : undefined);
  const syncStatus = deriveSyncStatus(instance, stats ?? undefined, configForDerive);
  const isSyncing = syncStatus === 'syncing';

  const { progress: syncProgress } = useConnectorSyncProgress(
    instance._key,
    instance.status,
    Boolean(instance._key) && instance.supportsSync
  );
  const showReindexFailedAction =
    Boolean(recordsStatus && recordsStatus.failed > 0) && !showStatsShimmer;
  const showManualIndexAction =
    Boolean(recordsStatus && recordsStatus.autoIndexOff > 0) && !showStatsShimmer;

  // Navigate to All Records page with filters for this connector
  const navigateToRecords = useCallback(
    (indexingStatuses?: IndexingStatus[]) => {
      const connectorId = instance._key;
      if (!connectorId) return;

      // Build URL with filter params (URL is source of truth)
      const params = new URLSearchParams();
      params.set('view', 'all-records');
      params.set('connectorIds', connectorId);
      if (indexingStatuses && indexingStatuses.length > 0) {
        params.set('indexingStatus', indexingStatuses.join(','));
      }

      // Close the panel and navigate
      closeInstancePanel();
      router.push(`/knowledge-base?${params.toString()}`);
    },
    [instance._key, closeInstancePanel, router]
  );

  const handleOverviewRefreshStats = useCallback(async () => {
    const connectorId = instance._key;
    if (!connectorId || isRefreshStatsBusy) return;
    try {
      setIsRefreshStatsBusy(true);
      await fetchInstanceStats(connectorId, { force: true });
      addToast({
        variant: 'success',
        title: t('workspace.connectors.overview.refreshStatsSuccess'),
      });
      if (isElectron() && isLocalFsConnectorType(instance.type)) {
        const rootPath = extractLocalFsRootPath(instanceConfigs[connectorId]);
        if (rootPath) {
          await startElectronLocalSync({
            connectorId,
            connectorName: instance.name,
            rootPath,
            ...buildLocalFsWatcherOptionsFromConnectorConfig(instanceConfigs[connectorId]),
            ...buildLocalSyncScheduleFromConnectorConfig(
              instanceConfigs[connectorId],
              instance.type
            ),
          });
          const status = await getElectronLocalSyncStatus(connectorId);
          if (status) {
            setLocalSyncStatus(connectorId, status);
          }
        }
      }
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.connectors.overview.refreshStatsError'),
      });
    } finally {
      setIsRefreshStatsBusy(false);
    }
  }, [
    instance._key,
    instance.type,
    instance.name,
    isRefreshStatsBusy,
    addToast,
    t,
    instanceConfigs,
    setLocalSyncStatus,
    fetchInstanceStats,
  ]);

  const handleOverviewResync = useCallback(async () => {
    const connectorId = instance._key;
    if (!connectorId || !instance.isActive || isHeaderSyncBusy) return;
    try {
      setIsHeaderSyncBusy(true);
      const outcome = await runConnectorResync({
        connectorId,
        connectorType: instance.type,
      });
      if (outcome.kind === 'requires-desktop') {
        addToast({
          variant: 'info',
          title: 'Open the Pipeshub desktop app on the machine that owns this folder to resync.',
        });
        return;
      }
      addToast({ variant: 'success', title: 'Sync started' });
      bumpCatalogRefresh();
    } catch (error) {
      console.error('Failed to start sync', { connectorId, error });
      addToast({ variant: 'error', title: 'Failed to start sync' });
    } finally {
      setIsHeaderSyncBusy(false);
    }
  }, [instance._key, instance.type, instance.isActive, isHeaderSyncBusy, addToast, bumpCatalogRefresh]);

  const handleReindexFailed = useCallback(async () => {
    const connectorId = instance._key;
    if (!connectorId || !instance.isActive || isReindexFailedBusy) return;
    try {
      setIsReindexFailedBusy(true);
      await ConnectorsApi.reindexFailedConnector(connectorId, instance.type, ['FAILED']);
      addToast({ variant: 'success', title: 'Reindexing failed records…' });
      await fetchInstanceStats(connectorId, { force: true });
    } catch (error) {
      console.error('Failed to reindex failed records', { connectorId, error });
      addToast({ variant: 'error', title: 'Failed to reindex failed records' });
    } finally {
      setIsReindexFailedBusy(false);
    }
  }, [instance._key, instance.type, instance.isActive, isReindexFailedBusy, addToast, fetchInstanceStats]);

  const handleManualIndex = useCallback(async () => {
    const connectorId = instance._key;
    if (!connectorId || !instance.isActive || isManualIndexBusy) return;
    try {
      setIsManualIndexBusy(true);
      await ConnectorsApi.reindexFailedConnector(connectorId, instance.type, [
        'AUTO_INDEX_OFF',
      ]);
      addToast({ variant: 'success', title: 'Indexing manual-indexing records…' });
      await fetchInstanceStats(connectorId, { force: true });
    } catch (error) {
      console.error('Failed to start manual indexing', { connectorId, error });
      addToast({ variant: 'error', title: 'Failed to start manual indexing' });
    } finally {
      setIsManualIndexBusy(false);
    }
  }, [instance._key, instance.type, instance.isActive, isManualIndexBusy, addToast, fetchInstanceStats]);

  // Run-scoped progress is shown while a sync/indexing run is active; when idle
  // it collapses to nothing and the Records Status grid below is the coverage view.
  const syncProgressView = describeSyncProgress(syncProgress, instance.status);
  const showRunProgress = syncProgressView.mode === 'discovering' || syncProgressView.mode === 'indexing';
  const runData = syncProgress?.run;

  return (
    <Flex direction="column" gap="5" style={{ padding: '0' }}>
      {/* ── Current sync progress (run-scoped) ── */}
      {showRunProgress && (
        <Flex
          direction="column"
          gap="3"
          style={{
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            padding: 16,
          }}
        >
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Current sync
          </Text>
          <ConnectorSyncProgress
            progress={syncProgress}
            status={instance.status}
            variant="detail"
          />
          {runData && (
            <Flex gap="4" wrap="wrap">
              <SyncBreakdownItem label="Queued this sync" value={runData.discovered} />
              <SyncBreakdownItem label="Indexed" value={runData.indexed} />
              {runData.failed > 0 && (
                <SyncBreakdownItem label="Failed" value={runData.failed} tone="amber" />
              )}
              {runData.skipped > 0 && (
                <SyncBreakdownItem label="Skipped" value={runData.skipped} />
              )}
            </Flex>
          )}
        </Flex>
      )}

      {/* ── Overall Records section (lifetime coverage) ── */}
      <Flex
        direction="column"
        gap="3"
        style={{
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          padding: 16,
        }}
      >
        <Flex align="start" gap="2">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)', flex: 1, minWidth: 0 }}>
            Overall records
          </Text>
          {instance.isActive && (
            <Flex
              direction="column"
              align="end"
              gap="1"
              style={{ marginLeft: 'auto', flexShrink: 0 }}
            >
              <Flex align="center" gap="1">
                <StatusActionButton
                  label="Sync now"
                  icon="sync"
                  onClick={() => void handleOverviewResync()}
                  disabled={isHeaderSyncBusy || reindexActionsBusy}
                  loading={isHeaderSyncBusy}
                />
                <StatusActionButton
                  label={t('action.refresh')}
                  icon="refresh"
                  onClick={() => void handleOverviewRefreshStats()}
                  disabled={isRefreshStatsBusy || reindexActionsBusy}
                  loading={isRefreshStatsBusy}
                />
              </Flex>
              {(showReindexFailedAction || showManualIndexAction) && recordsStatus && (
                <Flex align="center" gap="1">
                  {showReindexFailedAction && (
                    <IndexActionButton
                      label={`Reindex failed (${recordsStatus.failed})`}
                      icon="error_outline"
                      color="orange"
                      iconColor="var(--orange-11)"
                      onClick={() => void handleReindexFailed()}
                      disabled={reindexActionsBusy || isHeaderSyncBusy}
                      loading={isReindexFailedBusy}
                    />
                  )}
                  {showManualIndexAction && (
                    <IndexActionButton
                      label={`Manual index (${recordsStatus.autoIndexOff})`}
                      icon="touch_app"
                      color="gray"
                      iconColor="var(--gray-11)"
                      onClick={() => void handleManualIndex()}
                      disabled={reindexActionsBusy || isHeaderSyncBusy}
                      loading={isManualIndexBusy}
                    />
                  )}
                </Flex>
              )}
            </Flex>
          )}
        </Flex>

        {localSyncStatus && (
          <Flex align="center" justify="between" style={{ marginBottom: 4 }}>
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.connectors.overview.localWatcherLabel')}
            </Text>
            <Text size="1" style={{ color: 'var(--gray-11)' }}>
              {t('workspace.connectors.overview.localWatcherStatus', {
                state: localSyncStatus.watcherState,
                pending: localSyncStatus.pendingCount,
                failed: localSyncStatus.failedCount,
              })}
            </Text>
          </Flex>
        )}

        {showStatsShimmer ? (
          <OverviewStatsGridShimmer />
        ) : recordsStatus ? (
        <Flex direction="column" gap="2">
          {/* Row 1: Total + Completed */}
          <Flex gap="2" style={{ width: '100%' }}>
            <StatCard
              label={t('workspace.connectors.overview.statTotal')}
              value={recordsStatus.total}
              subtitle={t('workspace.connectors.overview.statTotalSub')}
              onClick={() => navigateToRecords()}
            />
            <StatCard
              label={t('workspace.connectors.overview.statCompleted')}
              value={recordsStatus.completed}
              subtitle={t('workspace.connectors.overview.statCompletedSub')}
              onClick={() => navigateToRecords(['COMPLETED'])}
            />
          </Flex>
          {/* Row 2: Failed, Processing, Queued, Manual indexing */}
          <Flex gap="2" style={{ width: '100%' }}>
            <StatCard
              label={t('status.failed')}
              value={recordsStatus.failed}
              subtitle={t('workspace.connectors.overview.statFailedSub')}
              valueColor={recordsStatus.failed > 0 ? 'var(--red-11)' : undefined}
              onClick={() => navigateToRecords(['FAILED'])}
            />
            <StatCard
              label={t('status.processing')}
              value={recordsStatus.inProgress}
              subtitle={t('workspace.connectors.overview.statInProgressSub')}
              onClick={() => navigateToRecords(['IN_PROGRESS'])}
            />
            <StatCard
              label={t('workspace.connectors.overview.statQueued')}
              value={recordsStatus.queued}
              subtitle={t('workspace.connectors.overview.statQueuedSub')}
              onClick={() => navigateToRecords(['QUEUED'])}
            />
            <StatCard
              label={t('workspace.connectors.overview.statManualIndexing')}
              value={recordsStatus.autoIndexOff}
              subtitle={t('workspace.connectors.overview.statManualIndexingSub')}
              onClick={() => navigateToRecords(['AUTO_INDEX_OFF'])}
            />
          </Flex>
          {/* Row 3 (last): Empty, Unsupported, Not started */}
          <Flex gap="2" style={{ width: '100%' }}>
            <StatCard
              label={t('workspace.connectors.overview.statEmpty')}
              value={recordsStatus.empty}
              subtitle={t('workspace.connectors.overview.statEmptySub')}
              onClick={() => navigateToRecords(['EMPTY'])}
            />
            <StatCard
              label={t('workspace.connectors.overview.statUnsupported')}
              value={recordsStatus.unsupported}
              subtitle={t('workspace.connectors.overview.statUnsupportedSub')}
              onClick={() => navigateToRecords(['FILE_TYPE_NOT_SUPPORTED'])}
            />
            <StatCard
              label={t('workspace.connectors.overview.statNotStarted')}
              value={recordsStatus.notStarted}
              subtitle={t('workspace.connectors.overview.statNotStartedSub')}
              onClick={() => navigateToRecords(['NOT_STARTED'])}
            />
          </Flex>
        </Flex>
        ) : null}
          </Flex>

          {/* ── Indexed Records by Type section ── */}
          <Flex direction="column" gap="3">
        <Flex align="center" justify="between">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.connectors.overview.recordsByType')}
          </Text>
          {showStatsShimmer ? (
            <OverviewTypesBadgeShimmer />
          ) : (
            <Badge variant="soft" color="gray" size="1">
              {byRecordType.length} Types
            </Badge>
          )}
        </Flex>

        {showStatsShimmer ? (
          <OverviewRecordTypesShimmer />
        ) : byRecordType.length === 0 ? (
          <Text size="2" style={{ color: 'var(--gray-9)' }}>
            {t('workspace.connectors.overview.noRecordTypes')}
          </Text>
        ) : (
          <Flex direction="column" gap="1">
            {byRecordType.map((rt) => (
              <Flex
                key={rt.recordType}
                align="center"
                justify="between"
                style={{
                  padding: 'var(--space-2) 0',
                  borderBottom: '1px solid var(--gray-a3)',
                }}
              >
                <Text size="2" style={{ color: 'var(--gray-12)' }}>
                  {formatSnakeCaseTitle(rt.recordType)}
                </Text>
                <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }}>
                  {rt.total}
                </Text>
              </Flex>
            ))}
          </Flex>
        )}
      </Flex>
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

const INDEX_ACTION_ICON_SIZE = 16;

/** Soft indexing actions (orange / gray). */
function IndexActionButton({
  label,
  icon,
  color,
  iconColor,
  onClick,
  disabled,
  loading,
}: {
  label: string;
  icon: string;
  color: 'orange' | 'gray';
  iconColor: string;
  onClick?: () => void;
  disabled?: boolean;
  loading?: boolean;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const isBusy = disabled || loading;
  const backgroundColor =
    color === 'orange'
      ? isHovered && !isBusy
        ? 'var(--orange-a4)'
        : 'var(--orange-a3)'
      : isHovered && !isBusy
        ? 'var(--gray-a4)'
        : 'var(--gray-a3)';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isBusy}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        height: 'var(--space-5)',
        padding: '0 var(--space-2)',
        borderRadius: 'max(var(--radius-1), var(--radius-full))',
        border: 'none',
        backgroundColor,
        color: iconColor,
        fontSize: 'var(--font-size-1)',
        fontWeight: 500,
        lineHeight: 'var(--line-height-1)',
        width: 'max-content',
        maxWidth: '100%',
        flexShrink: 0,
        whiteSpace: 'nowrap',
        cursor: loading ? 'wait' : disabled ? 'not-allowed' : 'pointer',
        opacity: disabled && !loading ? 0.6 : 1,
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name={icon} size={INDEX_ACTION_ICON_SIZE} color={iconColor} />
      {label}
    </button>
  );
}

function StatusActionButton({
  label,
  icon,
  onClick,
  disabled,
  loading,
}: {
  label: string;
  icon: string;
  onClick?: () => void;
  disabled?: boolean;
  loading?: boolean;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const isDisabled = disabled || loading;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isDisabled}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        height: 24,
        padding: '0 8px',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--gray-a4)',
        backgroundColor: isHovered && !isDisabled ? 'var(--gray-a3)' : 'transparent',
        color: 'var(--gray-11)',
        fontSize: 12,
        fontWeight: 500,
        cursor: loading ? 'wait' : isDisabled ? 'not-allowed' : 'pointer',
        opacity: isDisabled ? 0.6 : 1,
        transition: 'background-color 150ms ease',
        whiteSpace: 'nowrap',
      }}
    >
      <MaterialIcon name={icon} size={12} color="var(--gray-11)" />
      {label}
    </button>
  );
}

function StatCard({
  label,
  value,
  subtitle,
  valueColor,
  onClick,
}: {
  label: string;
  value: number;
  subtitle: string;
  valueColor?: string;
  onClick?: () => void;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const isClickable = !!onClick;

  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="2"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        flex: 1,
        padding: 'var(--space-6) var(--space-4)',
        backgroundColor: isHovered && isClickable ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        cursor: isClickable ? 'pointer' : 'default',
        transition: 'background-color 150ms ease',
      }}
    >
      <Text
        size="2"
        weight="medium"
        style={{
          color: 'var(--gray-12)',
          textAlign: 'center',
          width: '100%',
        }}
      >
        {label}
      </Text>
      <Flex direction="column" align="center" gap="2" style={{ width: '100%' }}>
        <Text
          size="6"
          weight="medium"
          style={{ color: valueColor || 'var(--gray-12)', textAlign: 'center', width: '100%' }}
        >
          {value}
        </Text>
        <Text
          size="1"
          style={{ color: 'var(--gray-10)', textAlign: 'center', width: '100%' }}
        >
          {subtitle}
        </Text>
      </Flex>
    </Flex>
  );
}
