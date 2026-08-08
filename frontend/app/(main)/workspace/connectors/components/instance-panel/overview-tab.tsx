'use client';

import React, { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { Flex, Text } from '@radix-ui/themes';
import { useConnectorsStore } from '../../store';
import { ConnectorsApi } from '../../api';
import { fetchInstanceStats } from '../../utils/fetch-instance-stats';
import { useConnectorSyncProgress } from '../../utils/use-connector-sync-progress';
import { ConnectorSyncProgress, describeSyncProgress } from '../connector-sync-progress';
import { useToastStore } from '@/lib/store/toast-store';
import {
  runConnectorResync,
  isConnectorSyncInProgressError,
  isConnectorSyncLockedError,
} from '../../utils/connector-sync-actions';
import { useSyncConflictGuard } from '../../utils/use-sync-conflict-guard';
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
  LocalSyncStatus,
} from '../../types';
import { IndexingStatsPanel } from '@/app/components/indexing-stats/indexing-stats-panel';

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

  const { progress: syncProgress } = useConnectorSyncProgress(
    instance._key,
    instance.status,
    Boolean(instance._key) && instance.supportsSync
  );

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
  ]);

  const { guard: syncConflictGuard, dialog: syncConflictDialog } = useSyncConflictGuard();

  const runOverviewResync = useCallback(
    async (force: boolean) => {
      const connectorId = instance._key;
      if (!connectorId) return;
      try {
        setIsHeaderSyncBusy(true);
        const outcome = await runConnectorResync({
          connectorId,
          connectorType: instance.type,
          force,
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
        if (
          isConnectorSyncInProgressError(error) ||
          isConnectorSyncLockedError(error)
        ) {
          throw error;
        }
        console.error('Failed to start sync', { connectorId, error });
        addToast({ variant: 'error', title: 'Failed to start sync' });
      } finally {
        setIsHeaderSyncBusy(false);
      }
    },
    [instance._key, instance.type, addToast, bumpCatalogRefresh]
  );

  const handleOverviewResync = useCallback(async () => {
    if (!instance._key || !instance.isActive || isHeaderSyncBusy) return;
    await syncConflictGuard(runOverviewResync, {
      requestedFullSync: false,
      currentStatus: instance.status,
    });
  }, [
    instance._key,
    instance.isActive,
    instance.status,
    isHeaderSyncBusy,
    runOverviewResync,
    syncConflictGuard,
  ]);

  const handleReindexFailed = useCallback(async () => {
    const connectorId = instance._key;
    if (!connectorId || !instance.isActive || isReindexFailedBusy) return;
    try {
      setIsReindexFailedBusy(true);
      await ConnectorsApi.reindexConnector(connectorId, ['FAILED']);
      addToast({ variant: 'success', title: 'Reindexing failed records…' });
      await fetchInstanceStats(connectorId, { force: true });
    } catch (error) {
      console.error('Failed to reindex failed records', { connectorId, error });
      addToast({ variant: 'error', title: 'Failed to reindex failed records' });
    } finally {
      setIsReindexFailedBusy(false);
    }
  }, [instance._key, instance.isActive, isReindexFailedBusy, addToast]);

  const handleManualIndex = useCallback(async () => {
    const connectorId = instance._key;
    if (!connectorId || !instance.isActive || isManualIndexBusy) return;
    try {
      setIsManualIndexBusy(true);
      await ConnectorsApi.reindexConnector(connectorId, ['AUTO_INDEX_OFF']);
      addToast({ variant: 'success', title: 'Indexing manual-indexing records…' });
      await fetchInstanceStats(connectorId, { force: true });
    } catch (error) {
      console.error('Failed to start manual indexing', { connectorId, error });
      addToast({ variant: 'error', title: 'Failed to start manual indexing' });
    } finally {
      setIsManualIndexBusy(false);
    }
  }, [instance._key, instance.isActive, isManualIndexBusy, addToast]);

  // Run-scoped progress is shown while a sync/indexing run is active; when idle
  // it collapses to nothing and the Records Status grid below is the coverage view.
  const syncProgressView = describeSyncProgress(syncProgress, instance.status);
  const showRunProgress =
    syncProgressView.mode === 'discovering' ||
    syncProgressView.mode === 'indexing' ||
    syncProgressView.mode === 'failed';
  const runData = syncProgress?.run;
  const showRunBreakdown = showRunProgress && syncProgressView.mode !== 'failed';

  return (
    <Flex direction="column" gap="5" style={{ padding: '0' }}>
      {syncConflictDialog}
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
            {syncProgressView.mode === 'failed'
              ? t('workspace.connectors.syncProgress.lastSync', { defaultValue: 'Last sync' })
              : t('workspace.connectors.syncProgress.currentSync', { defaultValue: 'Current sync' })}
          </Text>
          <ConnectorSyncProgress
            progress={syncProgress}
            status={instance.status}
            variant="detail"
          />
          {syncProgressView.mode === 'discovering' && syncProgressView.subtitle && (
            <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px' }}>
              {t(syncProgressView.subtitleKey ?? '', {
                defaultValue: syncProgressView.subtitle,
                ...syncProgressView.subtitleParams,
              })}
            </Text>
          )}
          {showRunBreakdown && runData && (
            <Flex gap="4" wrap="wrap">
              <SyncBreakdownItem
                label={t('workspace.connectors.syncProgress.breakdownScanned', { defaultValue: 'Scanned' })}
                value={runData.discovered + runData.unchanged + runData.failed}
              />
              <SyncBreakdownItem
                label={t('workspace.connectors.syncProgress.breakdownUnchanged', { defaultValue: 'Unchanged' })}
                value={runData.unchanged}
              />
              <SyncBreakdownItem
                label={t('workspace.connectors.syncProgress.breakdownQueued', { defaultValue: 'New/changed queued' })}
                value={runData.discovered}
              />
              <SyncBreakdownItem
                label={t('workspace.connectors.syncProgress.indexed', { defaultValue: 'Indexed' })}
                value={runData.indexed}
              />
              {runData.failed > 0 && (
                <SyncBreakdownItem
                  label={t('workspace.connectors.syncProgress.breakdownFailed', { defaultValue: 'Failed' })}
                  value={runData.failed}
                  tone="amber"
                />
              )}
              {runData.skipped > 0 && (
                <SyncBreakdownItem
                  label={t('workspace.connectors.syncProgress.breakdownSkipped', { defaultValue: 'Skipped' })}
                  value={runData.skipped}
                />
              )}
            </Flex>
          )}
        </Flex>
      )}

      {/* Local watcher status */}
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

      {/* Shared stats panel (from main) */}
      <IndexingStatsPanel
        stats={stats}
        loading={statsLoading}
        onRefresh={handleOverviewRefreshStats}
        onReindexFailed={handleReindexFailed}
        onManualIndex={handleManualIndex}
        onNavigateToRecords={navigateToRecords}
        onSync={instance.isActive ? handleOverviewResync : undefined}
        showSyncActions={instance.isActive}
        isRefreshBusy={isRefreshStatsBusy}
        isSyncBusy={isHeaderSyncBusy}
        isReindexFailedBusy={isReindexFailedBusy}
        isManualIndexBusy={isManualIndexBusy}
      />
    </Flex>
  );
}
