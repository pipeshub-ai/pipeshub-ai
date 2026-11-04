'use client';

import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Badge, Grid } from '@radix-ui/themes';
import {
  deriveClassificationStatus,
  deriveEntitiesStatus,
  deriveRecordsStatus,
} from './derive-records-status';
import { AiEnrichmentSection } from './ai-enrichment-section';
import { IndexActionButton } from './index-action-button';
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
  /** Re-runs only the classify stage for records whose classification failed. */
  onRetryFailedClassification?: () => Promise<void>;
  /** Re-runs only named-entity extraction for records whose entities stage failed. */
  onRetryFailedEntities?: () => Promise<void>;
  onNavigateToRecords?: (statuses?: IndexingStatus[]) => void;
  /** Connector-only: shows a "Sync now" button beside Refresh. Omit for KB collections. */
  onSync?: () => Promise<void>;
  /**
   * Connector-only: when supplied, replaces "Sync now" with Stop. Supplied only
   * while a sync is actually running, so its presence is the signal. KB
   * collections never pass it.
   */
  onStop?: () => Promise<void>;
  /** "Stop sync" vs "Stop full sync" — the caller knows which run is in flight. */
  stopLabel?: string;
  showSyncActions?: boolean;
  isRefreshBusy?: boolean;
  isSyncBusy?: boolean;
  isStopBusy?: boolean;
  isReindexFailedBusy?: boolean;
  isManualIndexBusy?: boolean;
  isRetryClassificationBusy?: boolean;
  isRetryEntitiesBusy?: boolean;
}

export function IndexingStatsPanel({
  stats,
  loading = false,
  onRefresh,
  onReindexFailed,
  onManualIndex,
  onRetryFailedClassification,
  onRetryFailedEntities,
  onNavigateToRecords,
  onSync,
  onStop,
  stopLabel,
  showSyncActions = false,
  isRefreshBusy = false,
  isSyncBusy = false,
  isStopBusy = false,
  isReindexFailedBusy = false,
  isManualIndexBusy = false,
  isRetryClassificationBusy = false,
  isRetryEntitiesBusy = false,
}: IndexingStatsPanelProps) {
  const { t } = useTranslation();
  const showStatsShimmer = loading || isRefreshBusy;
  const reindexActionsBusy =
    isReindexFailedBusy || isManualIndexBusy || isRetryClassificationBusy || isRetryEntitiesBusy;

  const recordsStatus = useMemo(
    () => (showStatsShimmer ? null : deriveRecordsStatus(stats)),
    [stats, showStatsShimmer]
  );
  const classificationStatus = useMemo(
    () => (showStatsShimmer ? null : deriveClassificationStatus(stats)),
    [stats, showStatsShimmer]
  );
  const entitiesStatus = useMemo(
    () => (showStatsShimmer ? null : deriveEntitiesStatus(stats)),
    [stats, showStatsShimmer]
  );

  const byRecordType = stats?.byRecordType ?? [];
  const showReindexFailedAction = Boolean(recordsStatus && recordsStatus.failed > 0) && !showStatsShimmer;
  const showManualIndexAction = Boolean(recordsStatus && recordsStatus.autoIndexOff > 0) && !showStatsShimmer;
  // Held here so a Refresh, which unmounts the section while stats reload, keeps it open.
  const [enrichmentOpen, setEnrichmentOpen] = useState(false);

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
        {/* Wraps: when the actions do not fit beside the title (phones, long retry labels),
            they move to their own row instead of squeezing the title to one letter per line. */}
        <Flex align="start" gap="2" wrap="wrap">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)', flex: '1 1 160px', minWidth: 0 }}>
            {t('workspace.connectors.overview.recordsStatus')}
          </Text>
          {showSyncActions && onRefresh && (
            <Flex
              direction="column"
              align="end"
              gap="1"
              style={{ marginLeft: 'auto', maxWidth: '100%', minWidth: 0 }}
            >
              <Flex align="center" gap="1" wrap="wrap" justify="end">
                {onStop ? (
                  <StatusActionButton
                    label={
                      isStopBusy
                        ? t('workspace.connectors.stopSync.stopping', {
                            defaultValue: 'Stopping…',
                          })
                        : (stopLabel ??
                          t('workspace.connectors.stopSync.label', {
                            defaultValue: 'Stop sync',
                          }))
                    }
                    icon="stop_circle"
                    onClick={() => void onStop()}
                    disabled={isStopBusy}
                    loading={isStopBusy}
                  />
                ) : (
                  onSync && (
                    <StatusActionButton
                      label="Sync now"
                      icon="sync"
                      onClick={() => void onSync()}
                      disabled={isSyncBusy || reindexActionsBusy}
                      loading={isSyncBusy}
                    />
                  )
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
                <Flex align="center" gap="1" wrap="wrap" justify="end">
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
            <Grid columns="2" gap="2" width="100%">
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
            </Grid>
            {/* Row 2: Failed, Processing, Queued, Manual indexing — two per row on phones */}
            <Grid columns={{ initial: '2', sm: '4' }} gap="2" width="100%">
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
            </Grid>
            {/* Row 3: Empty, Unsupported, Password protected, Not started */}
            <Grid columns={{ initial: '2', sm: '4' }} gap="2" width="100%">
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
                label={t('workspace.connectors.overview.statPasswordProtected')}
                value={recordsStatus.passwordProtected}
                subtitle={t('workspace.connectors.overview.statPasswordProtectedSub')}
                onClick={onNavigateToRecords ? () => onNavigateToRecords(['PASSWORD_PROTECTED']) : undefined}
              />
              <StatCard
                label={t('workspace.connectors.overview.statNotStarted')}
                value={recordsStatus.notStarted}
                subtitle={t('workspace.connectors.overview.statNotStartedSub')}
                onClick={onNavigateToRecords ? () => onNavigateToRecords(['NOT_STARTED']) : undefined}
              />
            </Grid>
          </Flex>
        ) : null}

        {classificationStatus ? (
          <AiEnrichmentSection
            classification={classificationStatus}
            entities={entitiesStatus}
            open={enrichmentOpen}
            onOpenChange={setEnrichmentOpen}
            onRetryFailedClassification={
              showSyncActions && onRetryFailedClassification
                ? () => void onRetryFailedClassification()
                : undefined
            }
            onRetryFailedEntities={
              showSyncActions && onRetryFailedEntities ? () => void onRetryFailedEntities() : undefined
            }
            isRetryClassificationBusy={isRetryClassificationBusy}
            isRetryEntitiesBusy={isRetryEntitiesBusy}
            retryDisabled={reindexActionsBusy || isSyncBusy}
          />
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
