'use client';

import type { CSSProperties } from 'react';
import { Flex, Box } from '@radix-ui/themes';

const SHIMMER: CSSProperties = {
  animation: 'shimmer-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
};

function ShimmerBar({ style }: { style?: CSSProperties }) {
  return (
    <Box
      style={{
        borderRadius: 'var(--radius-1)',
        backgroundColor: 'var(--gray-a4)',
        ...SHIMMER,
        ...style,
      }}
    />
  );
}

function StatCardShimmer({ tall }: { tall?: boolean }) {
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="3"
      style={{
        flex: 1,
        minWidth: 0,
        padding: tall ? 'var(--space-6) var(--space-4)' : 'var(--space-4)',
        backgroundColor: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        minHeight: tall ? 118 : 92,
      }}
    >
      <ShimmerBar style={{ width: '50%', height: 10 }} />
      <ShimmerBar style={{ width: '32%', height: tall ? 26 : 20 }} />
      <ShimmerBar style={{ width: '70%', height: 8 }} />
    </Flex>
  );
}

/** Skeleton for the Records Status stat grid (matches loaded layout). */
export function OverviewStatsGridShimmer() {
  return (
    <Flex direction="column" gap="2" style={{ width: '100%' }} aria-hidden>
      <Flex gap="2" style={{ width: '100%' }}>
        <StatCardShimmer tall />
        <StatCardShimmer tall />
      </Flex>
      <Flex gap="2" style={{ width: '100%' }}>
        <StatCardShimmer />
        <StatCardShimmer />
        <StatCardShimmer />
        <StatCardShimmer />
      </Flex>
      <Flex gap="2" style={{ width: '100%' }}>
        <StatCardShimmer />
        <StatCardShimmer />
        <StatCardShimmer />
      </Flex>
    </Flex>
  );
}

/** Skeleton rows for Records by Type list. */
export function OverviewRecordTypesShimmer({ rows = 4 }: { rows?: number }) {
  const labelWidths = ['58%', '42%', '52%', '48%'];
  return (
    <Flex direction="column" gap="1" aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <Flex
          key={i}
          align="center"
          justify="between"
          style={{
            padding: 'var(--space-2) 0',
            borderBottom: '1px solid var(--gray-a3)',
          }}
        >
          <ShimmerBar
            style={{
              width: labelWidths[i % labelWidths.length],
              maxWidth: 160,
              height: 12,
            }}
          />
          <ShimmerBar style={{ width: 40, height: 12, flexShrink: 0 }} />
        </Flex>
      ))}
    </Flex>
  );
}

/** Small pill placeholder for the "N Types" badge. */
export function OverviewTypesBadgeShimmer() {
  return (
    <Box
      aria-hidden
      style={{
        width: 56,
        height: 20,
        borderRadius: 'var(--radius-full)',
        backgroundColor: 'var(--gray-a4)',
        ...SHIMMER,
      }}
    />
  );
}
