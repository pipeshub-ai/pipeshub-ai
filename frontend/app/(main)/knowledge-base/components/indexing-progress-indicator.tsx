'use client';

import { useEffect, useState } from 'react';
import { Box, Flex, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { getIndexStatusIcon } from '@/lib/utils/index-status-icon';
import type { ConnectorSyncStatus, IndexingRollup } from '../types';
import {
  PROGRESS_STEPS,
  getIndexingProgressView,
  getRollupProgressView,
  isActiveIndexingStatus,
  type IndexingProgressInput,
} from '../utils/indexing-progress';

export function IndexingProgressIndicator({
  record,
  compact = false,
}: {
  record: IndexingProgressInput;
  compact?: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!isActiveIndexingStatus(record.indexingStatus)) return undefined;
    const interval = window.setInterval(() => setNow(Date.now()), 2000);
    return () => window.clearInterval(interval);
  }, [record.indexingStatus]);

  const view = getIndexingProgressView(record, now);
  const activeColor = 'var(--blue-9)';
  const activeText = 'var(--blue-11)';
  const isMoving = view.isActive;
  const barBackground = isMoving
    ? `linear-gradient(90deg, ${activeColor} 0%, ${activeColor} 35%, var(--blue-7) 50%, ${activeColor} 65%, ${activeColor} 100%)`
    : activeColor;

  return (
    <Flex direction="column" gap="1" style={{ minWidth: compact ? 180 : 240, maxWidth: compact ? 240 : 320 }}>
      <Flex align="center" justify="between" gap="2">
        <Text size="1" weight="medium" style={{ color: activeText, whiteSpace: 'nowrap' }}>
          In progress
        </Text>
        <Text size="1" weight="medium" style={{ color: activeText, whiteSpace: 'nowrap' }}>
          {view.percent}%
        </Text>
      </Flex>
      <Flex align="center" gap="1" style={{ minWidth: 0 }}>
        {PROGRESS_STEPS.map((label, index) => {
          const isReached = index <= view.stepIndex;
          const color = isReached ? activeColor : 'var(--slate-6)';
          const textColor = isReached ? activeText : 'var(--slate-9)';
          return (
            <Flex key={label} align="center" gap="1" style={{ minWidth: 0 }}>
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
                {label}
              </Text>
            </Flex>
          );
        })}
      </Flex>
      <Box style={{ height: 3, borderRadius: '9999px', background: 'var(--slate-4)', overflow: 'hidden' }}>
        <Box
          style={{
            width: `${view.percent}%`,
            height: '100%',
            borderRadius: '9999px',
            background: barBackground,
            backgroundSize: isMoving ? '200% 100%' : undefined,
            animation: isMoving ? 'shimmer-sweep 1.4s linear infinite' : undefined,
            transition: 'width 0.25s ease',
          }}
        />
      </Box>
      {view.detail ? (
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
          {view.detail}
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
  const view = getRollupProgressView(rollup);

  if (inline) {
    if (!view.isActive) {
      // Clean success mirrors a single record: just the green check, "Indexed" on hover.
      if (!view.hasErrors) {
        return (
          <Tooltip content="Indexed" side="top" delayDuration={200}>
            <Flex align="center" style={{ minWidth: 0 }}>
              <MaterialIcon name={getIndexStatusIcon('COMPLETED')} size={16} color="var(--emerald-11)" />
            </Flex>
          </Tooltip>
        );
      }
      // Failures: icon + aggregate breakdown; keep the same text in the tooltip.
      return (
        <Tooltip content={view.detail} side="top" delayDuration={200}>
          <Flex align="center" gap="1" style={{ minWidth: 0 }}>
            <MaterialIcon name={getIndexStatusIcon('FAILED')} size={16} color="var(--amber-11)" />
            {view.detail ? (
              <Text size="1" weight="medium" style={{ color: 'var(--amber-11)', whiteSpace: 'nowrap' }}>
                {view.detail}
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
          Indexing {view.percent}%
        </Text>
        <Box style={{ width: 72, height: 3, borderRadius: '9999px', background: 'var(--slate-4)', overflow: 'hidden', flexShrink: 0 }}>
          <Box
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
          {view.label}
        </Text>
      </Flex>
    );
  }

  if (!view.isActive) {
    // Clean success mirrors a single record: just the green check, "Indexed" on hover.
    if (!view.hasErrors) {
      return (
        <Tooltip content="Indexed" side="top" delayDuration={200}>
          <Flex align="center" justify="center" style={{ minWidth: 0 }}>
            <MaterialIcon name={getIndexStatusIcon('COMPLETED')} size={16} color="var(--emerald-11)" />
          </Flex>
        </Tooltip>
      );
    }
    // Failures: icon + aggregate breakdown; keep the same text in the tooltip.
    return (
      <Tooltip content={view.detail} side="top" delayDuration={200}>
        <Flex align="center" gap="1" style={{ minWidth: 0 }}>
          <MaterialIcon name={getIndexStatusIcon('FAILED')} size={16} color="var(--amber-11)" />
          {view.detail ? (
            <Text size="1" weight="medium" style={{ color: 'var(--amber-11)', whiteSpace: 'nowrap' }}>
              {view.detail}
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
          {view.label}
        </Text>
        <Text size="1" weight="medium" style={{ color: activeText, whiteSpace: 'nowrap' }}>
          {view.percent}%
        </Text>
      </Flex>
      <Box style={{ height: 3, borderRadius: '9999px', background: 'var(--slate-4)', overflow: 'hidden' }}>
        <Box
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
      {view.detail ? (
        <Text size="1" style={{ color: 'var(--amber-11)', whiteSpace: 'nowrap' }}>
          {view.detail}
        </Text>
      ) : null}
    </Flex>
  );
}

/** True while the owning connector is fetching from its source. */
export function isActiveConnectorSync(status?: ConnectorSyncStatus | null): boolean {
  return status === 'SYNCING' || status === 'FULL_SYNCING';
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
  if (!isActiveConnectorSync(syncStatus)) return null;

  const label = syncStatus === 'FULL_SYNCING' ? 'Full sync…' : 'Syncing…';
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
    <Flex align="center" gap="1" style={{ minWidth: 0 }}>
      {spinner}
      <Text size="1" weight="medium" style={{ color, whiteSpace: 'nowrap' }}>
        {label}
      </Text>
    </Flex>
  );
}
