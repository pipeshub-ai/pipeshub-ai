'use client';

import { Flex, Box } from '@radix-ui/themes';

const SHIMMER = {
  animation: 'shimmer-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
} as const;

const ROW_H = 24;
const ICON_SHIM = 14;
const LABEL_H = 8;

/** Replaces the expand chevron while a row’s children are loading (no Lottie). */
export function SidebarChevronSlotShimmer() {
  return (
    <Box
      style={{
        width: 14,
        height: 14,
        borderRadius: 'var(--radius-full)',
        backgroundColor: 'var(--slate-a5)',
        flexShrink: 0,
        ...SHIMMER,
      }}
    />
  );
}

/**
 * Placeholder rows while sidebar list data loads — compact “item row” layout
 * (icon + label bar per row), similar density to {@link FolderTreeItem} rows.
 */
export function SidebarListShimmerRows({
  count = 4,
  compact,
}: {
  count?: number;
  /** Tighter padding for legacy `components/sidebar` sections */
  compact?: boolean;
}) {
  const labelMaxWidths = ['78%', '64%', '88%', '56%', '72%', '82%'];
  const padding = compact ? 'var(--space-1) var(--space-2)' : 'var(--space-1) var(--space-5)';
  return (
    <Flex direction="column" gap="0" style={{ padding, width: '100%', minWidth: 0 }}>
      {Array.from({ length: count }, (_, i) => (
        <Flex
          key={i}
          align="center"
          gap="1"
          style={{
            height: `${ROW_H}px`,
            width: '100%',
            minWidth: 0,
            boxSizing: 'border-box',
          }}
        >
          <Box
            style={{
              width: ICON_SHIM,
              height: ICON_SHIM,
              borderRadius: 'var(--radius-1)',
              backgroundColor: 'var(--slate-a5)',
              flexShrink: 0,
              ...SHIMMER,
            }}
          />
          <Box
            style={{
              height: `${LABEL_H}px`,
              flex: 1,
              minWidth: 0,
              maxWidth: labelMaxWidths[i % labelMaxWidths.length],
              borderRadius: 'var(--radius-1)',
              backgroundColor: 'var(--slate-a4)',
              ...SHIMMER,
            }}
          />
        </Flex>
      ))}
    </Flex>
  );
}
