'use client';

import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { IndexActionButton } from './index-action-button';
import { stageApplicableCount, type StageProgress } from './derive-records-status';

const COMPLETED_COLOR = 'var(--accent-9)';
const WAITING_COLOR = 'var(--accent-4)';
// red-11, not red-9: red-9 and accent-9 are indistinguishable under protanopia.
const FAILED_COLOR = 'var(--red-11)';
// Wide enough that a single failure among thousands still shows.
const MIN_SEGMENT_PX = 4;
const CHEVRON_SIZE = 18;
// Header side inset: matches the (44 - 20) / 2 = 12px above and below the title.
const HEADER_INSET_X = 'var(--space-3)';
// Lines the body up under the title, past the chevron.
const BODY_INDENT = `calc(${HEADER_INSET_X} + ${CHEVRON_SIZE}px + var(--space-2))`;

export interface AiEnrichmentSectionProps {
  classification: StageProgress;
  /** Null when the backend sends no entities stage. */
  entities: StageProgress | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRetryFailedClassification?: () => void;
  onRetryFailedEntities?: () => void;
  isRetryClassificationBusy?: boolean;
  isRetryEntitiesBusy?: boolean;
  /** Another reindex or retry is in flight, or a sync is being started. */
  retryDisabled?: boolean;
}

/**
 * Classification and entity extraction run after a record is searchable, so the
 * Completed card above does not wait for them. Collapsed by default; the header badge
 * still surfaces failures.
 */
export function AiEnrichmentSection({
  classification,
  entities,
  open,
  onOpenChange,
  onRetryFailedClassification,
  onRetryFailedEntities,
  isRetryClassificationBusy = false,
  isRetryEntitiesBusy = false,
  retryDisabled = false,
}: AiEnrichmentSectionProps) {
  const { t } = useTranslation();
  const bodyId = useId();
  const [isHeaderHovered, setIsHeaderHovered] = useState(false);

  // With named-entity extraction off every record reads SKIPPED, so the row would say nothing.
  const showEntities = Boolean(entities && stageApplicableCount(entities) > 0);
  const stages = showEntities && entities ? [classification, entities] : [classification];

  return (
    <Flex
      direction="column"
      gap="3"
      style={{ borderTop: '1px solid var(--olive-3)', paddingTop: 'var(--space-2)' }}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => onOpenChange(!open)}
        onMouseEnter={() => setIsHeaderHovered(true)}
        onMouseLeave={() => setIsHeaderHovered(false)}
        style={{
          appearance: 'none',
          margin: 0,
          font: 'inherit',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-2)',
          width: '100%',
          minHeight: 44,
          padding: `0 ${HEADER_INSET_X}`,
          border: 'none',
          borderRadius: 'var(--radius-2)',
          backgroundColor: isHeaderHovered ? 'var(--olive-3)' : 'transparent',
          color: 'var(--gray-12)',
          cursor: 'pointer',
          textAlign: 'left',
          transition: 'background-color 150ms ease',
        }}
      >
        <span aria-hidden style={{ display: 'inline-flex' }}>
          <MaterialIcon
            name="chevron_right"
            size={CHEVRON_SIZE}
            color="var(--gray-11)"
            style={{ transform: open ? 'rotate(90deg)' : undefined, transition: 'transform 150ms ease' }}
          />
        </span>
        <Text size="2" weight="medium" style={{ flex: 1, minWidth: 0 }}>
          {t('workspace.connectors.overview.enrichment.title')}
        </Text>
        <EnrichmentStatusBadge stages={stages} />
      </button>

      {open ? (
        <Flex
          id={bodyId}
          direction="column"
          gap="4"
          style={{ padding: `0 ${HEADER_INSET_X} var(--space-1) ${BODY_INDENT}` }}
        >
          <Text size="1" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.connectors.overview.enrichment.description')}
          </Text>
          <StageRow
            label={t('workspace.connectors.overview.classification')}
            hint={t('workspace.connectors.overview.enrichment.classificationHint')}
            progress={classification}
            retryLabel={`${t('workspace.connectors.overview.retryFailedClassification')} (${classification.failed})`}
            onRetry={onRetryFailedClassification}
            retryBusy={isRetryClassificationBusy}
            retryDisabled={retryDisabled}
          />
          {showEntities && entities ? (
            <StageRow
              label={t('workspace.connectors.overview.enrichment.entities')}
              hint={t('workspace.connectors.overview.enrichment.entitiesHint')}
              progress={entities}
              retryLabel={`${t('workspace.connectors.overview.retryFailedEntities')} (${entities.failed})`}
              onRetry={onRetryFailedEntities}
              retryBusy={isRetryEntitiesBusy}
              retryDisabled={retryDisabled}
            />
          ) : null}
        </Flex>
      ) : null}
    </Flex>
  );
}

function EnrichmentStatusBadge({ stages }: { stages: StageProgress[] }) {
  const { t } = useTranslation();
  const failed = stages.reduce((sum, s) => sum + s.failed, 0);
  const waiting = stages.reduce((sum, s) => sum + s.waiting, 0);
  const completed = stages.reduce((sum, s) => sum + s.completed, 0);

  if (failed > 0) {
    return (
      <Badge color="red" variant="soft" size="1">
        <span aria-hidden style={{ display: 'inline-flex' }}>
          <MaterialIcon name="error_outline" size={14} />
        </span>
        {t('workspace.connectors.overview.enrichment.failedCount', { count: failed })}
      </Badge>
    );
  }
  if (waiting > 0) {
    return (
      <Badge color="gray" variant="soft" size="1">
        <span aria-hidden style={{ display: 'inline-flex' }}>
          <MaterialIcon name="schedule" size={14} />
        </span>
        {t('workspace.connectors.overview.enrichment.inProgress')}
      </Badge>
    );
  }
  if (completed > 0) {
    return (
      <Badge color="green" variant="soft" size="1">
        <span aria-hidden style={{ display: 'inline-flex' }}>
          <MaterialIcon name="check_circle" size={14} />
        </span>
        {t('workspace.connectors.overview.enrichment.done')}
      </Badge>
    );
  }
  return null;
}

function StageRow({
  label,
  hint,
  progress,
  retryLabel,
  onRetry,
  retryBusy,
  retryDisabled,
}: {
  label: string;
  hint: string;
  progress: StageProgress;
  retryLabel: string;
  onRetry?: () => void;
  retryBusy: boolean;
  retryDisabled: boolean;
}) {
  const { t } = useTranslation();
  const applicable = stageApplicableCount(progress);
  const progressText = t('workspace.connectors.overview.enrichment.progress', {
    completed: progress.completed,
    total: applicable,
  });

  const legend: { key: string; label: string; value: number; color?: string }[] = [
    ...(applicable > 0
      ? [{ key: 'completed', label: t('workspace.connectors.overview.statCompleted'), value: progress.completed, color: COMPLETED_COLOR }]
      : []),
    ...(progress.waiting > 0
      ? [{ key: 'waiting', label: t('workspace.connectors.overview.enrichment.waiting'), value: progress.waiting, color: WAITING_COLOR }]
      : []),
    ...(progress.failed > 0
      ? [{ key: 'failed', label: t('status.failed'), value: progress.failed, color: FAILED_COLOR }]
      : []),
    // Skipped records need nothing from this stage, so they stay off the meter.
    ...(progress.skipped > 0
      ? [{ key: 'skipped', label: t('workspace.connectors.overview.enrichment.skipped'), value: progress.skipped }]
      : []),
  ];

  return (
    <Flex direction="column" gap="2">
      <Flex align="start" justify="between" gap="3">
        <Flex direction="column" style={{ minWidth: 0 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {label}
          </Text>
          <Text size="1" style={{ color: 'var(--gray-10)' }}>
            {hint}
          </Text>
        </Flex>
        {applicable > 0 ? (
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', whiteSpace: 'nowrap' }}>
            {progressText}
          </Text>
        ) : null}
      </Flex>

      {applicable > 0 ? (
        <StageMeter label={label} progress={progress} applicable={applicable} valueText={progressText} />
      ) : progress.skipped === 0 ? (
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {t('workspace.connectors.overview.enrichment.notStarted')}
        </Text>
      ) : null}

      {legend.length > 0 ? (
        <Flex align="center" gap="3" wrap="wrap">
          {legend.map((item) => (
            <Flex key={item.key} align="center" gap="1">
              {item.color ? (
                <span
                  aria-hidden
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: 2,
                    backgroundColor: item.color,
                    // The waiting swatch is a pale track step; the ring keeps it visible on the panel.
                    boxShadow: item.key === 'waiting' ? 'inset 0 0 0 1px var(--accent-a7)' : undefined,
                    flexShrink: 0,
                  }}
                />
              ) : null}
              <Text size="1" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {item.value}
              </Text>
              <Text size="1" style={{ color: 'var(--gray-10)' }}>
                {item.label}
              </Text>
            </Flex>
          ))}
          {progress.failed > 0 && onRetry ? (
            <span style={{ marginLeft: 'auto' }}>
              <IndexActionButton
                label={t('workspace.connectors.overview.enrichment.retry')}
                ariaLabel={retryLabel}
                icon="replay"
                color="orange"
                iconColor="var(--orange-11)"
                onClick={onRetry}
                disabled={retryDisabled}
                loading={retryBusy}
              />
            </span>
          ) : null}
        </Flex>
      ) : null}
    </Flex>
  );
}

function StageMeter({
  label,
  progress,
  applicable,
  valueText,
}: {
  label: string;
  progress: StageProgress;
  applicable: number;
  valueText: string;
}) {
  const segments = [
    { key: 'completed', value: progress.completed, color: COMPLETED_COLOR },
    { key: 'failed', value: progress.failed, color: FAILED_COLOR },
    { key: 'waiting', value: progress.waiting, color: WAITING_COLOR },
  ].filter((s) => s.value > 0);

  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={applicable}
      aria-valuenow={progress.completed}
      aria-valuetext={valueText}
      style={{
        display: 'flex',
        gap: 2,
        height: 8,
        borderRadius: 4,
        overflow: 'hidden',
      }}
    >
      {segments.map((s) => (
        <div
          key={s.key}
          data-segment={s.key}
          style={{ flex: `${s.value} 1 0`, minWidth: MIN_SEGMENT_PX, backgroundColor: s.color }}
        />
      ))}
    </div>
  );
}
