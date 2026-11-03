import { useLayoutEffect, useState, type CSSProperties } from 'react';
import {
  TREE_BASE_PADDING,
  TREE_INDENT_CSS_VAR,
  TREE_INDENT_PER_LEVEL,
  TREE_LINE_OPACITY_CSS_VAR,
} from '@/app/components/sidebar';
import type { FolderTreeNode } from '../types';

/** Room a row needs after its indent: chevron, icon, a readable stretch of the name, right padding. */
const MIN_ROW_CONTENT_WIDTH = 150;
const MIN_INDENT_PER_LEVEL = 4;
/** Below this the guide lines sit too close together to tell apart, so they are hidden. */
const MIN_INDENT_FOR_GUIDE_LINES = 8;

/** Depth of the deepest row currently shown (collapsed subtrees are not counted). */
export function deepestVisibleDepth(nodes: FolderTreeNode[], expandedFolders: Record<string, boolean>): number {
  let deepest = 0;
  for (const node of nodes) {
    deepest = Math.max(deepest, node.depth);
    if (expandedFolders[node.id] && node.children.length > 0) {
      deepest = Math.max(deepest, deepestVisibleDepth(node.children, expandedFolders));
    }
  }
  return deepest;
}

/** The widest per-level indent, up to the default, that still leaves every visible row room for its name. */
export function fittedIndent(containerWidth: number, deepestDepth: number): number {
  if (containerWidth <= 0 || deepestDepth <= 0) return TREE_INDENT_PER_LEVEL;
  const fitted = Math.floor((containerWidth - TREE_BASE_PADDING - MIN_ROW_CONTENT_WIDTH) / deepestDepth);
  return Math.min(TREE_INDENT_PER_LEVEL, Math.max(MIN_INDENT_PER_LEVEL, fitted));
}

/**
 * Tightens a folder tree's indent as it gets deep, so rows are never pushed out
 * of a fixed-width sidebar. Spread `style` and attach `ref` on the tree container.
 */
export function useFittedTreeIndent(nodes: FolderTreeNode[], expandedFolders: Record<string, boolean>) {
  // State, not a ref object: the container only mounts once the tree has rows,
  // and the observer has to attach when it does.
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);

  useLayoutEffect(() => {
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);

  const indent = fittedIndent(width, deepestVisibleDepth(nodes, expandedFolders));
  const style = {
    [TREE_INDENT_CSS_VAR]: `${indent}px`,
    [TREE_LINE_OPACITY_CSS_VAR]: indent < MIN_INDENT_FOR_GUIDE_LINES ? 0 : 1,
  } as CSSProperties;
  return { ref: setElement, style };
}
