'use client';

import { useRef, useSyncExternalStore } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, Tooltip, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { getIndexStatusIcon } from '@/lib/utils/index-status-icon';
import { isActiveConnectorSyncStatus } from '../../workspace/connectors/utils/sync-progress-view';
import type { ConnectorSyncStatus, IndexingRollup } from '../types';
import {
  PROGRESS_STEPS,
  PROGRESS_STEP_KEYS,
  getIndexingProgressView,
  getRollupProgressView,
  isActiveIndexingStatus,
  type IndexingProgressInput,
} from '../utils/indexing-progress';

let progressClockNow = Date.now();
let progressClockTimer: number | undefined;
const progressClockListeners = new Set<() => void>();

function subscribeProgressClock(listener: () => void): () => void {
  progressClockListeners.add(listener);
  if (progressClockTimer === undefined) {
    progressClockNow = Date.now();
    progressClockTimer = window.setInterval(() => {
      progressClockNow = Date.now();
      progressClockListeners.forEach((notify) => notify());
    }, 2000);
  }
  return () => {
    progressClockListeners.delete(listener);
    if (progressClockListeners.size === 0 && progressClockTimer !== undefined) {
      window.clearInterval(progressClockTimer);
      progressClockTimer = undefined;
    }
  };
}

const subscribeNoop = () => () => {};
const getProgressClockSnapshot = () => progressClockNow;

export function IndexingProgressIndicator({
  record,
  compact = false,
}: {
  record: IndexingProgressInput;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const active = isActiveIndexingStatus(record.indexingStatus);
  const needsEstimateClock =
    active &&
    record.indexingStage === 'EXTRACTING' &&
    (!record.indexingProgress ||
      !(record.indexingProgress.total > 0) ||
      (record.indexingProgress.phase != null && record.indexingProgress.phase !== 'extracting'));
  const localExtractionStartedAt = useRef<number | null>(null);
  if (needsEstimateClock && record.lastActivityTimestamp == null) {
    localExtractionStartedAt.current ??= Date.now();
  } else {
    localExtractionStartedAt.current = null;
  }
  // All visible active rows share one clock instead of allocating one interval
  // per row. Rows still update together, preserving smooth extraction estimates.
  const now = useSyncExternalStore(
    needsEstimateClock ? subscribeProgressClock : subscribeNoop,
    getProgressClockSnapshot,
    getProgressClockSnapshot
  );

  const view = getIndexingProgressView(
    localExtractionStartedAt.current == null
      ? record
      : { ...record, lastActivityTimestamp: localExtractionStartedAt.current },
    now
  );
  const label = view.labelKey ? t(view.labelKey, { defaultValue: view.label }) : view.label;
  const detail =
    view.detail && view.detailKey
      ? t(view.detailKey, { defaultValue: view.detail, ...view.detailParams })
      : view.detail;
  const recordKey = (record as { id?: string }).id ?? record;
  const progressRef = useRef({ recordKey, maxPercent: 0 });
  if (progressRef.current.recordKey !== recordKey) {
    progressRef.current = { recordKey, maxPercent: 0 };
  }
  if (view.isActive) {
    progressRef.current.maxPercent = Math.max(progressRef.current.maxPercent, view.percent);
  }
  const displayedPercent = view.isActive ? progressRef.current.maxPercent : view.percent;
  const activeColor = 'var(--blue-9)';
  const activeText = 'var(--blue-11)';
  const isMoving = view.isActive;
  const barBackground = isMoving
    ? `linear-gradient(90deg, ${activeColor} 0%, ${activeColor} 35%, var(--blue-7) 50%, ${activeColor} 65%, ${activeColor} 100%)`
    : activeColor;

  return (
    <Flex direction="column" gap="1" style={{ minWidth: compact ? 120 : 240, maxWidth: compact ? 240 : 320 }}>
      <VisuallyHidden>
        <span role="status" aria-live="polite">
          {label} {displayedPercent}%{detail ? `, ${detail}` : ''}
        </span>
      </VisuallyHidden>
      <Flex align="center" justify="between" gap="2">
        <Text size="1" weight="medium" style={{ color: activeText, whiteSpace: 'nowrap' }}>
          {label}
        </Text>
        <Text size="1" weight="medium" style={{ color: activeText, whiteSpace: 'nowrap' }}>
          {displayedPercent}%
        </Text>
      </Flex>
      <Flex align="center" gap="1" style={{ minWidth: 0 }}>
        {PROGRESS_STEPS.map((stepLabel, index) => {
          const isReached = index <= view.stepIndex;
          const color = isReached ? activeColor : 'var(--slate-6)';
          const textColor = isReached ? activeText : 'var(--slate-9)';
          return (
            <Flex key={stepLabel} align="center" gap="1" style={{ minWidth: 0 }}>
              <Box
                style={{
                  width: compact ? 6 : 7,
                  height: compact ? 6 : 7,
                  borderRadius: '9999px',
                  background: color,
                  flexShrink: 0,
                }}
              />
              <Text
                size="1"
                style={{
                  color: textColor,
                  lineHeight: 1,
                  whiteSpace: 'nowrap',
                }}
              >
                {t(`kb.indexingProgress.steps.${PROGRESS_STEP_KEYS[index]}`, {
                  defaultValue: stepLabel,
                })}
              </Text>
            </Flex>
          );
        })}
      </Flex>
      <Box style={{ height: 3, borderRadius: '9999px', background: 'var(--slate-4)', overflow: 'hidden' }}>
        <Box
          role="progressbar"
          aria-label={`${label} indexing progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={displayedPercent}
          aria-valuetext={`${label} ${displayedPercent}%`}
          style={{
            width: `${displayedPercent}%`,
            height: '100%',
            borderRadius: '9999px',
            background: barBackground,
            backgroundSize: isMoving ? '200% 100%' : undefined,
            animation: isMoving ? 'shimmer-sweep 1.4s linear infinite' : undefined,
            transition: 'width 0.25s ease',
          }}
        />
      </Box>
      {detail ? (
        <Text
          size="1"
          style={{
            color: activeText,
            display: '-webkit-box',
            lineHeight: '13px',
            overflow: 'hidden',
            WebkitBoxOrient: 'vertical',
            WebkitLineClamp: 2,
            whiteSpace: 'normal',
          }}
        >
          {detail}
        </Text>
      ) : null}
    </Flex>
  );
}

/**
 * Aggregated progress for a container row (folder / collection / connector),
 * driven by the subtree rollup. Active containers show a moving bar + percent;
 * settled ones show a compact "Indexed" summary, amber when some records failed.
 */
export function ContainerRollupIndicator({
  rollup,
  compact = false,
  inline = false,
}: {
  rollup: IndexingRollup;
  compact?: boolean;
  /** Single-line variant for tight spots like the header. */
  inline?: boolean;
}) {
  const { t } = useTranslation();
  const view = getRollupProgressView(rollup);
  const label = t(view.labelKey, { defaultValue: view.label, ...view.labelParams });
  const detail = view.detailParts
    ? view.detailParts.map((part) => t(part.key, part.params)).join(' · ')
    : view.detail;
  const indexedTooltip = t('kb.indexingProgress.indexedTooltip', { defaultValue: 'Indexed' });

  if (inline) {
    if (!view.isActive) {
      // Clean success mirrors a single record: just the green check, "Indexed" on hover.
      if (!view.hasErrors) {
        return (
          <Tooltip content={indexedTooltip} side="top" delayDuration={200}>
            <Flex align="center" style={{ minWidth: 0 }}>
              <MaterialIcon name={getIndexStatusIcon('COMPLETED')} size={16} color="var(--emerald-11)" />
            </Flex>
          </Tooltip>
        );
      }
      // Failures: icon + aggregate breakdown; keep the same text in the tooltip.
      return (
        <Tooltip content={detail} side="top" delayDuration={200}>
          <Flex align="center" gap="1" style={{ minWidth: 0 }}>
            <MaterialIcon name={getIndexStatusIcon('FAILED')} size={16} color="var(--amber-11)" />
            {detail ? (
              <Text size="1" weight="medium" style={{ color: 'var(--amber-11)', whiteSpace: 'nowrap' }}>
                {detail}
              </Text>
            ) : null}
          </Flex>
        </Tooltip>
      );
    }
    const activeColor = 'var(--blue-9)';
    const activeText = 'var(--blue-11)';
    return (
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Text size="1" weight="medium" style={{ color: activeText, whiteSpace: 'nowrap' }}>
          {t('kb.indexingProgress.indexingPercent', {
            defaultValue: 'Indexing {{percent}}%',
            percent: view.percent,
          })}
        </Text>
        <Box style={{ width: 72, height: 3, borderRadius: '9999px', background: 'var(--slate-4)', overflow: 'hidden', flexShrink: 0 }}>
          <Box
            role="progressbar"
            aria-label={label}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={view.percent}
            style={{
              width: `${view.percent}%`,
              height: '100%',
              borderRadius: '9999px',
              background: `linear-gradient(90deg, ${activeColor} 0%, ${activeColor} 35%, var(--blue-7) 50%, ${activeColor} 65%, ${activeColor} 100%)`,
              backgroundSize: '200% 100%',
              animation: 'shimmer-sweep 1.4s linear infinite',
              transition: 'width 0.25s ease',
            }}
          />
        </Box>
        <Text size="1" style={{ color: 'var(--slate-10)', whiteSpace: 'nowrap' }}>
          {label}
        </Text>
      </Flex>
    );
  }

  if (!view.isActive) {
    // Clean success mirrors a single record: just the green check, "Indexed" on hover.
    if (!view.hasErrors) {
      return (
        <Tooltip content={indexedTooltip} side="top" delayDuration={200}>
          <Flex align="center" justify="center" style={{ minWidth: 0 }}>
            <MaterialIcon name={getIndexStatusIcon('COMPLETED')} size={16} color="var(--emerald-11)" />
          </Flex>
        </Tooltip>
      );
    }
    // Failures: icon + aggregate breakdown; keep the same text in the tooltip.
    return (
      <Tooltip content={detail} side="top" delayDuration={200}>
        <Flex align="center" gap="1" style={{ minWidth: 0 }}>
          <MaterialIcon name={getIndexStatusIcon('FAILED')} size={16} color="var(--amber-11)" />
          {detail ? (
            <Text size="1" weight="medium" style={{ color: 'var(--amber-11)', whiteSpace: 'nowrap' }}>
              {detail}
            </Text>
          ) : null}
        </Flex>
      </Tooltip>
    );
  }

  const activeColor = 'var(--blue-9)';
  const activeText = 'var(--blue-11)';
  const barBackground = `linear-gradient(90deg, ${activeColor} 0%, ${activeColor} 35%, var(--blue-7) 50%, ${activeColor} 65%, ${activeColor} 100%)`;

  return (
    <Flex direction="column" gap="1" style={{ minWidth: compact ? 160 : 200, maxWidth: compact ? 220 : 300 }}>
      <Flex align="center" justify="between" gap="2">
        <Text size="1" weight="medium" style={{ color: activeText, whiteSpace: 'nowrap' }}>
          {label}
        </Text>
        <Text size="1" weight="medium" style={{ color: activeText, whiteSpace: 'nowrap' }}>
          {view.percent}%
        </Text>
      </Flex>
      <Box style={{ height: 3, borderRadius: '9999px', background: 'var(--slate-4)', overflow: 'hidden' }}>
        <Box
          role="progressbar"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={view.percent}
          style={{
            width: `${view.percent}%`,
            height: '100%',
            borderRadius: '9999px',
            background: barBackground,
            backgroundSize: '200% 100%',
            animation: 'shimmer-sweep 1.4s linear infinite',
            transition: 'width 0.25s ease',
          }}
        />
      </Box>
      {detail ? (
        <Text size="1" style={{ color: 'var(--amber-11)', whiteSpace: 'nowrap' }}>
          {detail}
        </Text>
      ) : null}
    </Flex>
  );
}

/** True while the owning connector is fetching from its source. */
export function isActiveConnectorSync(status?: ConnectorSyncStatus | null): boolean {
  return isActiveConnectorSyncStatus(status);
}

/**
 * "Syncing" badge for connector-origin containers. Sync (connector pulling from the
 * source) is distinct from indexing (processing pulled records), so it gets its own
 * indigo styling and never mixes with the blue indexing bar.
 *
 * - `chip` — bordered pill for the KB header, next to breadcrumbs.
 * - `pill` — compact inline variant for a connector row in list / grid.
 */
export function ConnectorSyncBadge({
  syncStatus,
  variant = 'pill',
}: {
  syncStatus?: ConnectorSyncStatus | null;
  variant?: 'chip' | 'pill';
}) {
  const { t } = useTranslation();
  if (!isActiveConnectorSync(syncStatus)) return null;

  const label =
    syncStatus === 'FULL_SYNCING'
      ? t('workspace.connectors.syncProgress.badgeFullSync', { defaultValue: 'Full sync…' })
      : t('workspace.connectors.syncProgress.badgeSyncing', { defaultValue: 'Syncing…' });
  const color = 'var(--indigo-11)';

  const spinner = (
    <Box
      style={{
        width: 9,
        height: 9,
        borderRadius: '9999px',
        border: '1.5px solid var(--indigo-6)',
        borderTopColor: color,
        animation: 'spin 0.8s linear infinite',
        flexShrink: 0,
      }}
    />
  );

  if (variant === 'chip') {
    return (
      <Flex
        role="status"
        aria-live="polite"
        align="center"
        gap="1"
        style={{
          padding: '2px 8px',
          borderRadius: '9999px',
          background: 'var(--indigo-3)',
          border: '1px solid var(--indigo-5)',
          flexShrink: 0,
        }}
      >
        {spinner}
        <Text size="1" weight="medium" style={{ color, whiteSpace: 'nowrap' }}>
          {label}
        </Text>
      </Flex>
    );
  }

  return (
    <Flex role="status" aria-live="polite" align="center" gap="1" style={{ minWidth: 0 }}>
      {spinner}
      <Text size="1" weight="medium" style={{ color, whiteSpace: 'nowrap' }}>
        {label}
      </Text>
    </Flex>
  );
}
