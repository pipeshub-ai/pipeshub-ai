'use client';

import { useEffect, useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import type { IndexingRollup } from '../types';
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
      const color = view.hasErrors ? 'var(--amber-11)' : 'var(--grass-11)';
      return (
        <Flex align="center" gap="1" style={{ minWidth: 0 }}>
          <Box style={{ width: 6, height: 6, borderRadius: '9999px', background: color, flexShrink: 0 }} />
          <Text size="1" weight="medium" style={{ color, whiteSpace: 'nowrap' }}>
            {view.hasErrors ? `Indexed · ${view.detail}` : 'Indexed'}
          </Text>
        </Flex>
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
    // Settled: keep it quiet. Green when clean, amber when some records failed.
    const color = view.hasErrors ? 'var(--amber-11)' : 'var(--grass-11)';
    return (
      <Flex direction="column" align="center" gap="1" style={{ minWidth: 0 }}>
        <Text size="1" weight="medium" style={{ color, whiteSpace: 'nowrap' }}>
          {view.hasErrors ? 'Indexed with errors' : 'Indexed'}
        </Text>
        {view.detail ? (
          <Text size="1" style={{ color: 'var(--slate-10)', whiteSpace: 'nowrap' }}>
            {view.detail}
          </Text>
        ) : (
          <Text size="1" style={{ color: 'var(--slate-9)', whiteSpace: 'nowrap' }}>
            {view.label}
          </Text>
        )}
      </Flex>
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
