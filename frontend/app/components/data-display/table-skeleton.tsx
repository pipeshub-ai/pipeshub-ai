'use client';

import React from 'react';
import { Box, Flex } from '@radix-ui/themes';

export interface TableSkeletonColumnShape {
  /** Fixed width (e.g. "80px" or "20%"). Omit for flex: 1 */
  width?: string;
  /** Minimum width */
  minWidth?: string;
  /** Relative width of the placeholder bar as 0-1. Default: 0.6 */
  barWidth?: number;
}

export interface TableSkeletonProps {
  /** Number of placeholder rows. Default: 6 */
  rows?: number;
  /** Column shapes — matches the real table's column layout. */
  columns?: TableSkeletonColumnShape[];
  /** Whether to reserve space for a row-action menu (⋯) column. Default: true */
  hasRowActions?: boolean;
  /** Whether to reserve space for a leading checkbox column. Default: true */
  hasCheckbox?: boolean;
  /** Row height in px. Default: 60 (matches EntityDataTable) */
  rowHeight?: number;
}

/**
 * TableSkeleton — N shimmering placeholder rows for tables that haven't
 * finished their initial load yet. Matches the layout conventions of
 * `EntityDataTable` (60px rows, 38px checkbox column, 80px actions column,
 * 8px horizontal padding between cells).
 *
 * Uses the global `@keyframes shimmer-pulse` defined in `app/globals.css`.
 */
export function TableSkeleton({
  rows = 6,
  columns = [{ width: '20%', minWidth: '160px' }, { barWidth: 0.8 }, { width: '120px' }, { width: '140px' }],
  hasRowActions = true,
  hasCheckbox = true,
  rowHeight = 60,
}: TableSkeletonProps) {
  return (
    <Box aria-busy="true" aria-live="polite" style={{ width: '100%' }}>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <Flex
          key={rowIndex}
          align="center"
          style={{
            height: rowHeight,
            borderBottom: '1px solid var(--olive-3)',
            backgroundColor: 'var(--olive-1)',
          }}
        >
          {hasCheckbox && (
            <Flex
              align="center"
              justify="center"
              style={{ width: 38, flexShrink: 0, padding: '0 8px' }}
            >
              <Box
                style={{
                  width: 16,
                  height: 16,
                  borderRadius: 3,
                  backgroundColor: 'var(--olive-4)',
                  animation: 'shimmer-pulse 1.4s ease-in-out infinite',
                  animationDelay: `${rowIndex * 80}ms`,
                }}
              />
            </Flex>
          )}

          {columns.map((col, i) => {
            const barWidth = col.barWidth ?? 0.6;
            return (
              <Flex
                key={i}
                align="center"
                style={{
                  width: col.width,
                  minWidth: col.minWidth,
                  flex: col.width ? undefined : 1,
                  padding: '0 8px',
                }}
              >
                <Box
                  style={{
                    height: 12,
                    width: `${Math.round(barWidth * 100)}%`,
                    borderRadius: 4,
                    backgroundColor: 'var(--olive-4)',
                    animation: 'shimmer-pulse 1.4s ease-in-out infinite',
                    animationDelay: `${(rowIndex * 4 + i) * 60}ms`,
                  }}
                />
              </Flex>
            );
          })}

          {hasRowActions && (
            <Box style={{ width: 80, flexShrink: 0 }} />
          )}
        </Flex>
      ))}
    </Box>
  );
}
