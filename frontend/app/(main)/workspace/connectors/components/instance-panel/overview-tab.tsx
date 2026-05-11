'use client';

import React, { useState, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { Flex, Text, Box, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useConnectorsStore } from '../../store';
import { ConnectorsApi } from '../../api';
import { useToastStore } from '@/lib/store/toast-store';
import { deriveSyncStatus } from '../instance-card/utils';
import type { IndexingStatus } from '@/app/(main)/knowledge-base/types';
import type {
  ConnectorInstance,
  ConnectorConfig,
  ConnectorStatsResponse,
  RecordsStatus,
} from '../../types';
import { formatSnakeCaseTitle } from '@/lib/utils/formatters';

// ========================================
// Props
// ========================================

interface OverviewTabProps {
  instance: ConnectorInstance;
  /** Stats data from GET /knowledgeBase/stats/{connectorId} */
  stats?: ConnectorStatsResponse['data'] | null;
  /** GET …/config — used to resolve auth type for OAuth-only UI rules */
  connectorConfig?: ConnectorConfig;
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

// ========================================
// OverviewTab
// ========================================

export function OverviewTab({ instance, stats, connectorConfig }: OverviewTabProps) {
  const { t } = useTranslation();
  const router = useRouter();
  const closeInstancePanel = useConnectorsStore((s) => s.closeInstancePanel);
  const addToast = useToastStore((s) => s.addToast);
  const bumpCatalogRefresh = useConnectorsStore((s) => s.bumpCatalogRefresh);
  const setInstanceStats = useConnectorsStore((s) => s.setInstanceStats);
  const [isRefreshStatsBusy, setIsRefreshStatsBusy] = useState(false);
  const [isReindexBusy, setIsReindexBusy] = useState(false);
  const recordsStatus = useMemo(() => deriveRecordsStatus(stats), [stats]);

  // Derive indexed records from byRecordType data
  const byRecordType = stats?.byRecordType ?? [];

  const syncStatus = deriveSyncStatus(instance, stats ?? undefined, connectorConfig);
  const isSyncing = syncStatus === 'syncing';
  const isSyncFailed = syncStatus === 'sync_failed';

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
      const res = await ConnectorsApi.getConnectorStats(connectorId);
      setInstanceStats(connectorId, res.data);
      addToast({
        variant: 'success',
        title: t('workspace.connectors.overview.refreshStatsSuccess'),
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.connectors.overview.refreshStatsError'),
      });
    } finally {
      setIsRefreshStatsBusy(false);
    }
  }, [instance._key, isRefreshStatsBusy, addToast, setInstanceStats, t]);

  const handleReindexFailed = useCallback(async () => {
    const connectorId = instance._key;
    if (!connectorId || !instance.isActive || isReindexBusy) return;
    try {
      setIsReindexBusy(true);
      await ConnectorsApi.reindexFailedConnector(connectorId, instance.type);
      addToast({ variant: 'success', title: 'Reindexing failed records…' });
      bumpCatalogRefresh();
    } catch {
      addToast({ variant: 'error', title: 'Failed to reindex' });
    } finally {
      setIsReindexBusy(false);
    }
  }, [instance._key, instance.type, instance.isActive, isReindexBusy, addToast, bumpCatalogRefresh]);

  // Show sync progress bar for syncing
  const showProgressBar = isSyncing && instance.syncProgress;

  return (
    <Flex direction="column" gap="5" style={{ padding: '0' }}>
      {/* ── Sync progress bar ── */}
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

      {/* ── Records Status section ── */}
      <Flex
        direction="column"
        gap="5"
        style={{
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          padding: 16,
        }}
      >
        <Flex align="center" justify="between">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.connectors.overview.recordsStatus')}
          </Text>
          {instance.isActive && (
            <Flex align="center" gap="1">
              {isSyncFailed && (
                <StatusActionButton
                  label="Reindex Failed"
                  icon="replay"
                  onClick={handleReindexFailed}
                  disabled={isReindexBusy}
                  loading={isReindexBusy}
                />
              )}
              <StatusActionButton
                label={t('action.refresh')}
                icon="refresh"
                onClick={() => void handleOverviewRefreshStats()}
                disabled={isRefreshStatsBusy}
                loading={isRefreshStatsBusy}
              />
            </Flex>
          )}
        </Flex>

        {/* Stats grid */}
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
      </Flex>

      {/* ── Indexed Records by Type section ── */}
      <Flex direction="column" gap="3">
        <Flex align="center" justify="between">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.connectors.overview.recordsByType')}
          </Text>
          <Badge variant="soft" color="gray" size="1">
            {byRecordType.length} Types
          </Badge>
        </Flex>

        {byRecordType.length === 0 ? (
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
        cursor: isDisabled ? 'not-allowed' : 'pointer',
        opacity: isDisabled ? 0.6 : 1,
        transition: 'background-color 150ms ease',
        whiteSpace: 'nowrap',
      }}
    >
      <MaterialIcon name={icon} size={12} color="var(--gray-11)" />
      {loading ? '…' : label}
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
