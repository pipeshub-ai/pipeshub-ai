import type {
  KnowledgeHubNode,
  EnhancedFolderTreeNode,
  CategorizedNodes,
  SidebarSection,
} from '../types';

/**
 * Determines sidebar section based on sharingStatus
 * - 'team' or 'shared' → SHARED
 * - 'private' or 'personal' → PRIVATE
 */
export function categorizeNode(node: KnowledgeHubNode): SidebarSection {
  if (node.sharingStatus === 'team' || node.sharingStatus === 'shared') return 'shared';
  return 'private';
}

/**
 * Convert API node to tree node structure
 */
export function nodeToTreeNode(
  node: KnowledgeHubNode,
  depth: number = 0,
  children: EnhancedFolderTreeNode[] = []
): EnhancedFolderTreeNode {
  return {
    id: node.id,
    name: node.name,
    children,
    isExpanded: false,
    depth,
    parentId: node.parentId,
    nodeType: node.nodeType,
    // Normalize to boolean — API may return null/undefined in some cases
    hasChildren: typeof node.hasChildren === 'boolean' ? node.hasChildren : false,
    isLoading: false,
    permission: node.permission,
    origin: node.origin,
    connector: node.connector,
  };
}

/**
 * Build hierarchical tree from flat node list
 */
export function buildTreeFromNodes(
  nodes: KnowledgeHubNode[],
  parentId: string | null = null,
  depth: number = 0
): EnhancedFolderTreeNode[] {
  const childNodes = nodes.filter((node) => node.parentId === parentId);

  return childNodes.map((node) => {
    const children = node.hasChildren ? buildTreeFromNodes(nodes, node.id, depth + 1) : [];
    return nodeToTreeNode(node, depth, children);
  });
}

/**
 * Categorize nodes into sidebar sections
 * @param nodes - The nodes to categorize (KB app children or root nodes)
 * @param rootParentId - The parentId that identifies top-level nodes (null for root nodes, 'apps/<id>' for KB app children)
 */
export function categorizeNodes(nodes: KnowledgeHubNode[], rootParentId: string | null = null): CategorizedNodes {
  // Filter out app nodes early in the categorization process
  const filteredNodes = nodes.filter((node) => node.nodeType !== 'app');

  const nodesBySection: Record<SidebarSection, KnowledgeHubNode[]> = {
    shared: [],
    private: [],
  };

  filteredNodes.forEach((node) => {
    const section = categorizeNode(node);
    nodesBySection[section].push(node);
  });

  return {
    shared: buildTreeFromNodes(nodesBySection.shared, rootParentId),
    private: buildTreeFromNodes(nodesBySection.private, rootParentId),
  };
}

/**
 * Merge lazy-loaded children into existing tree.
 * @param effectiveHasChildFolders - When provided, overwrites the parent node's hasChildren
 *   with the value derived from the fresh API response (e.g. counts).
 */
export function mergeChildrenIntoTree(
  tree: EnhancedFolderTreeNode[],
  parentId: string,
  children: KnowledgeHubNode[],
  effectiveHasChildFolders?: boolean
): EnhancedFolderTreeNode[] {
  return tree.map((node) => {
    if (node.id === parentId) {
      const childTreeNodes = children.map((child) => nodeToTreeNode(child, node.depth + 1));
      return {
        ...node,
        children: childTreeNodes,
        isLoading: false,
        ...(effectiveHasChildFolders !== undefined ? { hasChildren: effectiveHasChildFolders } : {}),
      };
    } else if (node.children.length > 0) {
      return {
        ...node,
        children: mergeChildrenIntoTree(
          node.children as EnhancedFolderTreeNode[],
          parentId,
          children,
          effectiveHasChildFolders
        ),
      };
    }
    return node;
  });
}
