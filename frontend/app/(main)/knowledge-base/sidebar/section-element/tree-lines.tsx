'use client';

import React from 'react';
import { Box } from '@radix-ui/themes';
import { TREE_LINE_OFFSET, TREE_LINE_OPACITY_CSS_VAR, treeIndentOffset } from '@/app/components/sidebar';

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
          left: treeIndentOffset(i, TREE_LINE_OFFSET),
          top: 0,
          bottom: 0,
          width: '1px',
          backgroundColor: 'var(--slate-6)',
          opacity: `var(${TREE_LINE_OPACITY_CSS_VAR}, 1)`,
          pointerEvents: 'none',
        }}
      />
    );
  }

  return lines;
}
