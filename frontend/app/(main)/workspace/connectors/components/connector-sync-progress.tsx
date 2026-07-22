'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import {
  describeIndexingQueueCompact,
  describeIndexingQueueDetail,
  shouldShowIndexingQueue,
} from '../utils/indexing-queue-copy';
import { describeSyncFailure, resolveFailureSummary } from '../utils/sync-failure-copy';
import { describeSyncProgress } from '../utils/sync-progress-view';
import type { ConnectorSyncProgress as ConnectorSyncProgressData } from '../types';

export { describeSyncProgress } from '../utils/sync-progress-view';
export type { SyncProgressView } from '../utils/sync-progress-view';

interface ConnectorSyncProgressProps {
  progress: ConnectorSyncProgressData | null | undefined;
  status?: string | null;
  /** 'card' is compact for list rows; 'detail' adds a top label/percent line. */
  variant?: 'card' | 'detail';
}

/**
 * Settled/terminal states rendered as a label/value row. In `card` variant the
 * label sits in the same 164px column the instance card uses for SYNC STRATEGY,
 * ENABLED BY and LAST SYNCED so "Status" aligns with the rows around it.
 */
function StatusRow({
  variant,
  label,
  children,
}: {
  variant: 'card' | 'detail';
  label: string;
  children: React.ReactNode;
}) {
  if (variant === 'detail') {
    return (
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Text
          size="1"
          weight="medium"
          style={{ color: 'var(--gray-10)', textTransform: 'uppercase', letterSpacing: '0.04px' }}
        >
          {label}
        </Text>
        {children}
      </Flex>
    );
  }
  return (
    <Flex align="center" gap="4">
      <Text
        size="1"
        weight="medium"
        style={{
          color: 'var(--slate-10)',
          width: 164,
          flexShrink: 0,
          textTransform: 'uppercase',
          letterSpacing: '0.04px',
          lineHeight: '16px',
        }}
      >
        {label}
      </Text>
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        {children}
      </Flex>
    </Flex>
  );
}

function IndeterminateBar({ label, valueText }: { label: string; valueText: string }) {
  return (
    <Box
      role="progressbar"
      aria-label={label}
      aria-valuetext={valueText}
      style={{
        position: 'relative',
        width: '100%',
        height: 6,
        borderRadius: 'var(--radius-full)',
        backgroundColor: 'var(--gray-a3)',
        overflow: 'hidden',
      }}
    >
      <Box
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          height: '100%',
          width: '30%',
          borderRadius: 'var(--radius-full)',
          backgroundColor: 'var(--indigo-9)',
          animation: 'connectorIndeterminate 1.3s ease-in-out infinite',
        }}
      />
    </Box>
  );
}

function DeterminateBar({ percent, label }: { percent: number; label: string }) {
  return (
    <Box
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.max(0, Math.min(100, percent))}
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
          width: `${Math.max(0, Math.min(100, percent))}%`,
          height: '100%',
          borderRadius: 'var(--radius-full)',
          // Sweeping gradient (shared shimmer-sweep keyframe) so the fill reads
          // as actively working rather than a static blue block.
          background:
            'linear-gradient(90deg, var(--blue-9) 0%, var(--blue-9) 35%, var(--blue-7) 50%, var(--blue-9) 65%, var(--blue-9) 100%)',
          backgroundSize: '200% 100%',
          animation: 'shimmer-sweep 1.4s linear infinite',
          transition: 'width 300ms ease',
        }}
      />
    </Box>
  );
}

export function ConnectorSyncProgress({
  progress,
  status,
  variant = 'card',
}: ConnectorSyncProgressProps) {
  const { t } = useTranslation();
  const view = describeSyncProgress(progress, status);
  if (view.mode === 'none') {
    return null;
  }

  const statusLabel = t('workspace.connectors.syncProgress.status', { defaultValue: 'Status' });
  const label = t(view.labelKey, { defaultValue: view.label, ...view.labelParams });

  if (view.mode === 'deleting') {
    return (
      <StatusRow variant={variant} label={statusLabel}>
        <Spinner size={14} color="var(--red-11)" ariaLabel={label} />
        <Text size="2" weight="medium" style={{ color: 'var(--red-11)', whiteSpace: 'nowrap' }}>
          {label}
        </Text>
      </StatusRow>
    );
  }

  if (view.mode === 'failed') {
    const failure = describeSyncFailure(view.failureCode);
    const failureTitle = t(`${failure.key}.title`, { defaultValue: failure.title });
    const summary = resolveFailureSummary(view.failureCode, view.failureReason);
    const summaryText = summary.key
      ? t(summary.key, { defaultValue: summary.text, ...summary.params })
      : summary.text;
    const remediation = t(`${failure.key}.remediation`, { defaultValue: failure.remediation });

    return (
      <Flex direction="column" gap="1" style={{ minWidth: 0, width: '100%' }}>
        <StatusRow variant={variant} label={statusLabel}>
          <MaterialIcon name="error" size={16} color="var(--red-11)" />
          <Text size="2" weight="medium" style={{ color: 'var(--red-11)', lineHeight: '20px' }}>
            {failureTitle}
          </Text>
          {view.failed > 0 && (
            <Text size="2" style={{ color: 'var(--amber-11)', whiteSpace: 'nowrap' }}>
              ·{' '}
              {t('workspace.connectors.syncProgress.failedCount', {
                defaultValue: '{{count}} failed',
                count: view.failed,
              })}
            </Text>
          )}
        </StatusRow>
        <Text
          size="1"
          style={{
            color: 'var(--slate-11)',
            lineHeight: '16px',
            paddingLeft: variant === 'card' ? 180 : 0,
          }}
        >
          {summaryText} {remediation}
        </Text>
      </Flex>
    );
  }

  if (view.mode === 'settled') {
    return (
      <StatusRow variant={variant} label={statusLabel}>
        <MaterialIcon name="check_circle" size={16} color="var(--emerald-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
          {label}
        </Text>
        {view.hasErrors && (
          <>
            <Text size="2" style={{ color: 'var(--gray-9)' }}>
              ·
            </Text>
            <Flex align="center" gap="1" style={{ minWidth: 0 }}>
              <MaterialIcon name="error_outline" size={14} color="var(--amber-11)" />
              <Text size="2" weight="medium" style={{ color: 'var(--amber-11)', whiteSpace: 'nowrap' }}>
                {t('workspace.connectors.syncProgress.failedCount', {
                  defaultValue: '{{count}} failed',
                  count: view.failed,
                })}
              </Text>
            </Flex>
          </>
        )}
      </StatusRow>
    );
  }

  const rightLabel =
    view.mode === 'indexing'
      ? `${view.percent}%`
      : view.mode === 'discovering' && view.detail
        ? t(view.detailKey ?? '', { defaultValue: view.detail, ...view.detailParams })
        : null;

  const showQueue = shouldShowIndexingQueue(progress?.indexingQueue, {
    indexing: view.mode === 'indexing',
  });
  const queue = progress?.indexingQueue;

  let queueLine: React.ReactNode = null;
  if (showQueue && queue) {
    if (variant === 'card') {
      const compact = describeIndexingQueueCompact();
      queueLine = (
        <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px' }}>
          {t(compact.key, { defaultValue: compact.text, ...compact.params })}
        </Text>
      );
    } else {
      const detail = describeIndexingQueueDetail(queue);
      const jobsText = t(detail.jobs.key, {
        defaultValue: detail.jobs.text,
        ...detail.jobs.params,
      });
      const etaText = detail.eta
        ? t(detail.eta.key, { defaultValue: detail.eta.text, ...detail.eta.params })
        : null;
      queueLine = (
        <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px' }}>
          {jobsText}
          {etaText ? ` · ${etaText}` : null}
        </Text>
      );
    }
  }

  return (
    <Flex direction="column" gap="2" style={{ width: '100%', minWidth: 0 }}>
      <Flex align="center" justify="between" gap="2">
        <Text
          role="status"
          aria-live="polite"
          size={variant === 'detail' ? '2' : '1'}
          weight="medium"
          style={{ color: 'var(--gray-12)', whiteSpace: 'nowrap' }}
        >
          {label}
        </Text>
        {rightLabel ? (
          <Text size="1" style={{ color: 'var(--gray-11)', whiteSpace: 'nowrap' }}>
            {rightLabel}
          </Text>
        ) : null}
      </Flex>
      {view.mode === 'indexing' && view.percent > 0 ? (
        <DeterminateBar percent={view.percent} label={label} />
      ) : (
        // At 0% (nothing terminal yet) a determinate fill is invisible, so show
        // the moving indeterminate bar until real progress lands.
        <IndeterminateBar
          label={label}
          valueText={t('workspace.connectors.syncProgress.calculating', {
            defaultValue: 'Progress is being calculated',
          })}
        />
      )}
      {queueLine}
    </Flex>
  );
}
