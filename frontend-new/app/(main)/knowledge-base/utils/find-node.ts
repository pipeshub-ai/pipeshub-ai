import type { CategorizedNodes, EnhancedFolderTreeNode } from '../types';

export interface FindNodeResult {
  node: EnhancedFolderTreeNode | null;
  rootKbId: string | null;
}

/**
 * Find a node by id in the categorized tree (shared + private).
 * Returns the node and its root KB ancestor id (null for top-level KB/app nodes).
 */
export function findNodeInCategorized(
  categorizedNodes: CategorizedNodes | null,
  targetId: string
): FindNodeResult {
  if (!categorizedNodes) return { node: null, rootKbId: null };

  const walk = (
    nodes: EnhancedFolderTreeNode[],
    currentRootKbId: string | null
  ): FindNodeResult | null => {
    for (const node of nodes) {
      if (node.id === targetId) {
        return { node, rootKbId: currentRootKbId };
      }
      if (node.children?.length) {
        const childRootKbId = currentRootKbId ?? node.id;
        const found = walk(node.children as EnhancedFolderTreeNode[], childRootKbId);
        if (found) return found;
      }
    }
    return null;
  };

  return (
    walk(categorizedNodes.shared, null) ??
    walk(categorizedNodes.private, null) ?? { node: null, rootKbId: null }
  );
}
