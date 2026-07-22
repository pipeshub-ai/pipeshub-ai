'use client';

import React, { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { Flex, Text, Box } from '@radix-ui/themes';
import { useConnectorsStore } from '../../store';
import { ConnectorsApi } from '../../api';
import { fetchInstanceStats } from '../../utils/fetch-instance-stats';
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
  const configForDerive =
    connectorConfig ?? (instance._key ? instanceConfigs[instance._key] : undefined);
  const syncStatus = deriveSyncStatus(instance, stats ?? undefined, configForDerive);
  const isSyncing = syncStatus === 'syncing';

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
      await ConnectorsApi.reindexConnector(connectorId, ['FAILED']);
      addToast({ variant: 'success', title: 'Reindexing failed records…' });
      await fetchInstanceStats(connectorId, { force: true });
    } catch (error) {
      console.error('Failed to reindex failed records', { connectorId, error });
      addToast({ variant: 'error', title: 'Failed to reindex failed records' });
    } finally {
      setIsReindexFailedBusy(false);
    }
  }, [instance._key, instance.isActive, isReindexFailedBusy, addToast, fetchInstanceStats]);

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
  }, [instance._key, instance.isActive, isManualIndexBusy, addToast, fetchInstanceStats]);

  // Show sync progress bar for syncing
  const showProgressBar = isSyncing && instance.syncProgress;

  return (
    <Flex direction="column" gap="5" style={{ padding: '0' }}>
      {/* Sync progress bar */}
      {showProgressBar && instance.syncProgress && (
        <Flex direction="column" gap="2">
          <Flex align="center" justify="between">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.connectors.overview.progressPercent', { n: instance.syncProgress.percentage ?? 0 })}
            </Text>
          </Flex>
          <Box
            style={{
              width: '100%',
              height: 6,
              borderRadius: 'var(--radius-full)',
              backgroundColor: 'var(--gray-a3)',
              overflow: 'hidden',
            }}
          >
            <Box
              style={{
                width: `${instance.syncProgress.percentage ?? 0}%`,
                height: '100%',
                borderRadius: 'var(--radius-full)',
                backgroundColor: 'var(--jade-9)',
                transition: 'width 300ms ease',
              }}
            />
          </Box>
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

      {/* Shared stats panel */}
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
