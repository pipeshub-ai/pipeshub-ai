'use client';

import React from 'react';
import { Box } from '@radix-ui/themes';
import {
  TREE_INDENT_PER_LEVEL,
  TREE_BASE_PADDING,
  TREE_LINE_OFFSET,
} from '@/app/components/sidebar';

/**
 * Renders vertical tree-indent lines for nested sidebar items.
 *
 * Each line is absolutely positioned so the parent must have
 * `position: 'relative'` on its container.
 *
 * @param depth   The nesting depth of the current node (0 = root)
 * @param startDepth  First depth level that should show a line (default 0).
 *                     Pass 1 to hide the root-level line.
 */
export function renderTreeLines(depth: number, startDepth: number = 0): React.ReactNode {
  if (depth === 0) return null;

  const lines: React.ReactNode[] = [];

  for (let i = startDepth; i < depth; i++) {
    lines.push(
      <Box
        key={`line-${i}`}
        style={{
          position: 'absolute',
          left: `${TREE_BASE_PADDING + i * TREE_INDENT_PER_LEVEL + TREE_LINE_OFFSET}px`,
          top: 0,
          bottom: 0,
          width: '1px',
          backgroundColor: 'var(--slate-6)',
          pointerEvents: 'none',
        }}
      />
    );
  }

  return lines;
}
