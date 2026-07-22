'use client';

import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Badge } from '@radix-ui/themes';
import { deriveRecordsStatus } from './derive-records-status';
import { StatCard } from './stat-card';
import {
  OverviewStatsGridShimmer,
  OverviewRecordTypesShimmer,
  OverviewTypesBadgeShimmer,
} from '@/app/(main)/workspace/connectors/components/instance-panel/overview-stats-shimmer';
import type { ConnectorStatsResponse } from '@/app/(main)/workspace/connectors/types';
import type { IndexingStatus } from '@/app/(main)/knowledge-base/types';
import { formatSnakeCaseTitle } from '@/lib/utils/formatters';

export interface IndexingStatsPanelProps {
  stats?: ConnectorStatsResponse['data'] | null;
  loading?: boolean;
  onRefresh?: () => Promise<void>;
  onReindexFailed?: () => Promise<void>;
  onManualIndex?: () => Promise<void>;
  onNavigateToRecords?: (statuses?: IndexingStatus[]) => void;
  /** Connector-only: shows a "Sync now" button beside Refresh. Omit for KB collections. */
  onSync?: () => Promise<void>;
  showSyncActions?: boolean;
  isRefreshBusy?: boolean;
  isSyncBusy?: boolean;
  isReindexFailedBusy?: boolean;
  isManualIndexBusy?: boolean;
}

export function IndexingStatsPanel({
  stats,
  loading = false,
  onRefresh,
  onReindexFailed,
  onManualIndex,
  onNavigateToRecords,
  onSync,
  showSyncActions = false,
  isRefreshBusy = false,
  isSyncBusy = false,
  isReindexFailedBusy = false,
  isManualIndexBusy = false,
}: IndexingStatsPanelProps) {
  const { t } = useTranslation();
  const showStatsShimmer = loading || isRefreshBusy;
  const reindexActionsBusy = isReindexFailedBusy || isManualIndexBusy;

  const recordsStatus = useMemo(
    () => (showStatsShimmer ? null : deriveRecordsStatus(stats)),
    [stats, showStatsShimmer]
  );

  const byRecordType = stats?.byRecordType ?? [];
  const showReindexFailedAction = Boolean(recordsStatus && recordsStatus.failed > 0) && !showStatsShimmer;
  const showManualIndexAction = Boolean(recordsStatus && recordsStatus.autoIndexOff > 0) && !showStatsShimmer;

  return (
    <Flex direction="column" gap="5" style={{ padding: '0' }}>
      {/* Records Status section */}
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
            {t('workspace.connectors.overview.recordsStatus')}
          </Text>
          {showSyncActions && onRefresh && (
            <Flex
              direction="column"
              align="end"
              gap="1"
              style={{ marginLeft: 'auto', flexShrink: 0 }}
            >
              <Flex align="center" gap="1">
                {onSync && (
                  <StatusActionButton
                    label="Sync now"
                    icon="sync"
                    onClick={() => void onSync()}
                    disabled={isSyncBusy || reindexActionsBusy}
                    loading={isSyncBusy}
                  />
                )}
                <StatusActionButton
                  label={t('action.refresh')}
                  icon="refresh"
                  onClick={() => void onRefresh()}
                  disabled={isRefreshBusy || reindexActionsBusy}
                  loading={isRefreshBusy}
                />
              </Flex>
              {(showReindexFailedAction || showManualIndexAction) && recordsStatus && (
                <Flex align="center" gap="1">
                  {showReindexFailedAction && onReindexFailed && (
                    <IndexActionButton
                      label={`Reindex failed (${recordsStatus.failed})`}
                      icon="error_outline"
                      color="orange"
                      iconColor="var(--orange-11)"
                      onClick={() => void onReindexFailed()}
                      disabled={reindexActionsBusy || isSyncBusy}
                      loading={isReindexFailedBusy}
                    />
                  )}
                  {showManualIndexAction && onManualIndex && (
                    <IndexActionButton
                      label={`Manual index (${recordsStatus.autoIndexOff})`}
                      icon="touch_app"
                      color="gray"
                      iconColor="var(--gray-11)"
                      onClick={() => void onManualIndex()}
                      disabled={reindexActionsBusy || isSyncBusy}
                      loading={isManualIndexBusy}
                    />
                  )}
                </Flex>
              )}
            </Flex>
          )}
        </Flex>

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
                onClick={onNavigateToRecords ? () => onNavigateToRecords() : undefined}
              />
              <StatCard
                label={t('workspace.connectors.overview.statCompleted')}
                value={recordsStatus.completed}
                subtitle={t('workspace.connectors.overview.statCompletedSub')}
                onClick={onNavigateToRecords ? () => onNavigateToRecords(['COMPLETED']) : undefined}
              />
            </Flex>
            {/* Row 2: Failed, Processing, Queued, Manual indexing */}
            <Flex gap="2" style={{ width: '100%' }}>
              <StatCard
                label={t('status.failed')}
                value={recordsStatus.failed}
                subtitle={t('workspace.connectors.overview.statFailedSub')}
                valueColor={recordsStatus.failed > 0 ? 'var(--red-11)' : undefined}
                onClick={onNavigateToRecords ? () => onNavigateToRecords(['FAILED']) : undefined}
              />
              <StatCard
                label={t('status.processing')}
                value={recordsStatus.inProgress}
                subtitle={t('workspace.connectors.overview.statInProgressSub')}
                onClick={onNavigateToRecords ? () => onNavigateToRecords(['IN_PROGRESS']) : undefined}
              />
              <StatCard
                label={t('workspace.connectors.overview.statQueued')}
                value={recordsStatus.queued}
                subtitle={t('workspace.connectors.overview.statQueuedSub')}
                onClick={onNavigateToRecords ? () => onNavigateToRecords(['QUEUED']) : undefined}
              />
              <StatCard
                label={t('workspace.connectors.overview.statManualIndexing')}
                value={recordsStatus.autoIndexOff}
                subtitle={t('workspace.connectors.overview.statManualIndexingSub')}
                onClick={onNavigateToRecords ? () => onNavigateToRecords(['AUTO_INDEX_OFF']) : undefined}
              />
            </Flex>
            {/* Row 3: Empty, Unsupported, Not started */}
            <Flex gap="2" style={{ width: '100%' }}>
              <StatCard
                label={t('workspace.connectors.overview.statEmpty')}
                value={recordsStatus.empty}
                subtitle={t('workspace.connectors.overview.statEmptySub')}
                onClick={onNavigateToRecords ? () => onNavigateToRecords(['EMPTY']) : undefined}
              />
              <StatCard
                label={t('workspace.connectors.overview.statUnsupported')}
                value={recordsStatus.unsupported}
                subtitle={t('workspace.connectors.overview.statUnsupportedSub')}
                onClick={onNavigateToRecords ? () => onNavigateToRecords(['FILE_TYPE_NOT_SUPPORTED']) : undefined}
              />
              <StatCard
                label={t('workspace.connectors.overview.statNotStarted')}
                value={recordsStatus.notStarted}
                subtitle={t('workspace.connectors.overview.statNotStartedSub')}
                onClick={onNavigateToRecords ? () => onNavigateToRecords(['NOT_STARTED']) : undefined}
              />
            </Flex>
          </Flex>
        ) : null}
      </Flex>

      {/* Indexed Records by Type section */}
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

// Helper components
const INDEX_ACTION_ICON_SIZE = 16;

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
  const [isHovered, setIsHovered] = React.useState(false);
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
  const [isHovered, setIsHovered] = React.useState(false);
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

// Import MaterialIcon
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
