import { describe, expect, it } from 'vitest';
import { deepestVisibleDepth, fittedIndent } from '../sidebar/use-fitted-tree-indent';
import type { FolderTreeNode } from '../types';

function chain(levels: number): FolderTreeNode[] {
  let children: FolderTreeNode[] = [];
  for (let depth = levels - 1; depth >= 0; depth--) {
    children = [{ id: `n${depth}`, name: `L${depth}`, depth, children } as FolderTreeNode];
  }
  return children;
}

describe('fitted tree indent', () => {
  it('keeps the default indent while the deepest row still fits', () => {
    expect(fittedIndent(232, 2)).toBe(20);
    expect(fittedIndent(0, 30)).toBe(20);
    expect(fittedIndent(232, 0)).toBe(20);
  });

  it('tightens the indent for a deep tree and never goes below the minimum', () => {
    expect(fittedIndent(440, 21)).toBe(13);
    expect(fittedIndent(232, 26)).toBe(4);
  });

  it('measures only rows that are shown: collapsed subtrees do not count', () => {
    const nodes = chain(6);
    expect(deepestVisibleDepth(nodes, {})).toBe(0);
    expect(deepestVisibleDepth(nodes, { n0: true, n1: true })).toBe(2);
    expect(deepestVisibleDepth(nodes, { n0: true, n1: true, n2: true, n3: true, n4: true })).toBe(5);
  });
});
