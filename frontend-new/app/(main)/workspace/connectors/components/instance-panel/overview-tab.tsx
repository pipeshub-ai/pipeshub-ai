'use client';

import React, { useState, useMemo, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Flex, Text, Box, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useKnowledgeBaseStore } from '@/app/(main)/knowledge-base/store';
import { useConnectorsStore } from '../../store';
import { ConnectorsApi } from '../../api';
import { useToastStore } from '@/lib/store/toast-store';
import { deriveSyncStatus } from '../instance-card/utils';
import { startConnectorSync } from '../../utils/connector-sync-actions';
import type { IndexingStatus } from '@/app/(main)/knowledge-base/types';
import type {
  ConnectorInstance,
  ConnectorStatsResponse,
  RecordsStatus,
} from '../../types';

// ========================================
// Props
// ========================================

interface OverviewTabProps {
  instance: ConnectorInstance;
  /** Stats data from GET /knowledgeBase/stats/{connectorId} */
  stats?: ConnectorStatsResponse['data'] | null;
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
  };
}

// ========================================
// OverviewTab
// ========================================

export function OverviewTab({ instance, stats }: OverviewTabProps) {
  const router = useRouter();
  const setAllRecordsFilter = useKnowledgeBaseStore((s) => s.setAllRecordsFilter);
  const closeInstancePanel = useConnectorsStore((s) => s.closeInstancePanel);
  const addToast = useToastStore((s) => s.addToast);
  const bumpCatalogRefresh = useConnectorsStore((s) => s.bumpCatalogRefresh);
  const [isStartingSync, setIsStartingSync] = useState(false);
  const [isHeaderSyncBusy, setIsHeaderSyncBusy] = useState(false);
  const [isReindexBusy, setIsReindexBusy] = useState(false);
  const recordsStatus = useMemo(() => deriveRecordsStatus(stats), [stats]);

  // Derive indexed records from byRecordType data
  const byRecordType = stats?.byRecordType ?? [];

  const syncStatus = deriveSyncStatus(instance, stats ?? undefined);
  const isReadyToSync  = syncStatus === 'ready_to_sync';
  const isSyncing      = syncStatus === 'syncing';
  const isSyncFailed   = syncStatus === 'sync_failed';

  // Navigate to All Records page with filters for this connector
  const navigateToRecords = useCallback(
    (indexingStatuses?: IndexingStatus[]) => {
      const connectorId = instance._key;
      if (!connectorId) return;

      // Set filters in the KB store before navigating
      setAllRecordsFilter({
        connectorIds: [connectorId],
        ...(indexingStatuses ? { indexingStatus: indexingStatuses } : {}),
      });

      // Close the panel and navigate
      closeInstancePanel();
      router.push('/knowledge-base?view=all-records');
    },
    [instance._key, setAllRecordsFilter, closeInstancePanel, router]
  );

  const handleStartSync = useCallback(async () => {
    const connectorId = instance._key;
    if (!connectorId || isStartingSync) return;
    try {
      setIsStartingSync(true);
      await startConnectorSync({
        _key: connectorId,
        type: instance.type,
        isActive: instance.isActive,
      });
      addToast({ variant: 'success', title: 'Sync started successfully' });
      bumpCatalogRefresh();
    } catch {
      addToast({ variant: 'error', title: 'Failed to start sync' });
    } finally {
      setIsStartingSync(false);
    }
  }, [instance._key, instance.type, instance.isActive, isStartingSync, addToast, bumpCatalogRefresh]);

  const handleOverviewResync = useCallback(async () => {
    const connectorId = instance._key;
    if (!connectorId || !instance.isActive || isHeaderSyncBusy) return;
    try {
      setIsHeaderSyncBusy(true);
      await ConnectorsApi.resyncConnector(connectorId, instance.type);
      addToast({ variant: 'success', title: 'Sync started' });
      bumpCatalogRefresh();
    } catch {
      addToast({ variant: 'error', title: 'Failed to start sync' });
    } finally {
      setIsHeaderSyncBusy(false);
    }
  }, [instance._key, instance.type, instance.isActive, isHeaderSyncBusy, addToast, bumpCatalogRefresh]);

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

  // Status banner for ready_to_sync
  const showReadyBanner = isReadyToSync;
  // Show sync progress bar for syncing
  const showProgressBar = isSyncing && instance.syncProgress;

  return (
    <Flex direction="column" gap="5" style={{ padding: '0' }}>
      {/* ── Ready to sync banner ── */}
      {showReadyBanner && (
        <Flex
          align="center"
          justify="between"
          style={{
            padding: '12px 16px',
            backgroundColor: 'var(--jade-a2)',
            border: '1px solid var(--jade-a4)',
            borderRadius: 'var(--radius-2)',
          }}
        >
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Instance configured and ready to sync!
            </Text>
            <Text size="1" style={{ color: 'var(--gray-11)' }}>
              Records are ready to be synced.
            </Text>
          </Flex>
          <button
            type="button"
            onClick={handleStartSync}
            disabled={isStartingSync}
            style={{
              appearance: 'none',
              margin: 0,
              font: 'inherit',
              outline: 'none',
              border: 'none',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              height: 28,
              padding: '0 10px',
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--jade-9)',
              color: 'white',
              fontSize: 12,
              fontWeight: 500,
              cursor: isStartingSync ? 'not-allowed' : 'pointer',
              opacity: isStartingSync ? 0.7 : 1,
              whiteSpace: 'nowrap',
            }}
          >
            <MaterialIcon name="sync" size={14} color="white" />
            {isStartingSync ? 'Starting...' : 'Start Syncing Now'}
          </button>
        </Flex>
      )}

      {/* ── Sync progress bar ── */}
      {showProgressBar && instance.syncProgress && (
        <Flex direction="column" gap="2">
          <Flex align="center" justify="between">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {instance.syncProgress.percentage ?? 0}% Complete
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
            Records Status
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
                label="Sync"
                icon="sync"
                onClick={handleOverviewResync}
                disabled={isHeaderSyncBusy}
                loading={isHeaderSyncBusy}
              />
            </Flex>
          )}
        </Flex>

        {/* Stats grid */}
        <Flex direction="column" gap="2">
          {/* Top row: Total + Failed */}
          <Flex gap="2" style={{ width: '100%' }}>
            <StatCard
              label="Total"
              value={recordsStatus.total}
              subtitle="Total records"
              onClick={() => navigateToRecords()}
            />
            <StatCard
              label="Failed"
              value={recordsStatus.failed}
              subtitle="Errors (API / permission issues)"
              valueColor={recordsStatus.failed > 0 ? 'var(--red-11)' : undefined}
              onClick={() => navigateToRecords(['FAILED'])}
            />
          </Flex>
          {/* Bottom row: Unsupported + In Progress + Not Started */}
          <Flex gap="2" style={{ width: '100%' }}>
            <StatCard
              label="Unsupported"
              value={recordsStatus.unsupported}
              subtitle="Can't be processed"
              onClick={() => navigateToRecords(['FILE_TYPE_NOT_SUPPORTED'])}
            />
            <StatCard
              label="In Progress"
              value={recordsStatus.inProgress}
              subtitle="Still being indexed"
              onClick={() => navigateToRecords(['IN_PROGRESS'])}
            />
            <StatCard
              label="Not Started"
              value={recordsStatus.notStarted}
              subtitle="Queued for sync"
              onClick={() => navigateToRecords(['NOT_STARTED'])}
            />
          </Flex>
        </Flex>
      </Flex>

      {/* ── Indexed Records by Type section ── */}
      <Flex direction="column" gap="3">
        <Flex align="center" justify="between">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Records by Type
          </Text>
          <Badge variant="soft" color="gray" size="1">
            {byRecordType.length} Types
          </Badge>
        </Flex>

        {byRecordType.length === 0 ? (
          <Text size="2" style={{ color: 'var(--gray-9)' }}>
            No record types available
          </Text>
        ) : (
          <Flex direction="column" gap="1">
            {byRecordType.map((rt) => (
              <Flex
                key={rt.recordType}
                align="center"
                justify="between"
                style={{
                  padding: '8px 0',
                  borderBottom: '1px solid var(--gray-a3)',
                }}
              >
                <Text size="2" style={{ color: 'var(--gray-12)' }}>
                  {rt.recordType}
                </Text>
                <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }}>
                  {rt.total}
                </Text>
              </Flex>
            ))}
          </Flex>
        )}
      </Flex>

      {/* ── Personal Records Indexed ── */}
      <Flex
        direction="column"
        gap="2"
        style={{
          padding: 16,
          backgroundColor: 'var(--gray-a2)',
          borderRadius: 'var(--radius-2)',
          border: '1px solid var(--gray-a3)',
        }}
      >
        <Flex align="center" gap="2">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Personal Records Indexed
          </Text>
          <MaterialIcon name="lock" size={14} color="var(--gray-9)" />
        </Flex>
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
        padding: '24px 16px',
        backgroundColor: isHovered && isClickable ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        cursor: isClickable ? 'pointer' : 'default',
        transition: 'background-color 150ms ease',
      }}
    >
      <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
        {label}
      </Text>
      <Flex direction="column" align="center" gap="2">
        <Text
          size="6"
          weight="medium"
          style={{ color: valueColor || 'var(--gray-12)' }}
        >
          {value}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)', textAlign: 'center' }}>
          {subtitle}
        </Text>
      </Flex>
    </Flex>
  );
}
