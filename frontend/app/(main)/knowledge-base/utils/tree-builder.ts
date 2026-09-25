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
    hasDescendants: typeof node.hasChildren === 'boolean' ? node.hasChildren : false,
    isLoading: false,
    permission: node.permission,
    origin: node.origin,
    connector: node.connector,
    subType: node.subType,
    extension: node.extension,
    mimeType: node.mimeType,
    indexingStatus: node.indexingStatus,
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
  const filteredNodes = nodes;

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
 * Sidebar expand chevron after an onlyContainers fetch.
 * Leaf records are omitted from these responses, so an empty list means no
 * expandable container rows — hide the chevron even when hasDescendants remains
 * true for reindex.
 */
export function effectiveHasChildrenAfterSidebarExpand(containerItems: unknown[]): boolean {
  return containerItems.length > 0;
}

/** Descendant flag for reindex — preserved when sidebar expand clears hasChildren. */
export function getTreeNodeDescendantsFlag(node: EnhancedFolderTreeNode): boolean {
  return node.hasDescendants ?? node.hasChildren;
}

/**
 * Merge lazy-loaded children into existing tree.
 * @param effectiveHasChildFolders - When provided, overwrites the parent node's hasChildren
 *   (sidebar expand chevron only; hasDescendants is preserved for reindex).
 */
/** True if any node in the tree matches `id` (recursive). */
export function treeHasNodeWithId(tree: EnhancedFolderTreeNode[], id: string): boolean {
  for (const node of tree) {
    if (node.id === id) return true;
    if (node.children?.length && treeHasNodeWithId(node.children as EnhancedFolderTreeNode[], id)) {
      return true;
    }
  }
  return false;
}

/** Ancestor ids shallow→deep to `targetId` (excludes `targetId`). */
export function findAncestorChainIds(
  nodes: EnhancedFolderTreeNode[],
  targetId: string
): string[] | null {
  const walk = (
    arr: EnhancedFolderTreeNode[],
    stack: string[]
  ): string[] | null => {
    for (const n of arr) {
      if (n.id === targetId) return stack;
      if (n.children?.length) {
        const hit = walk(n.children as EnhancedFolderTreeNode[], [...stack, n.id]);
        if (hit) return hit;
      }
    }
    return null;
  };
  return walk(nodes, []);
}

/**
 * Build sidebar roots for a connector (non-KB) app from a flat API child list.
 * Tries common parentId shapes used by the knowledge-hub API.
 */
export function buildConnectorAppSidebarTree(
  appId: string,
  items: KnowledgeHubNode[]
): EnhancedFolderTreeNode[] {
  const filtered = items.filter((n) => n.nodeType !== 'app');
  const appPrefix = `apps/${appId}`;
  const byAppPrefix = buildTreeFromNodes(filtered, appPrefix);
  if (byAppPrefix.length > 0) return byAppPrefix;
  const byAppId = buildTreeFromNodes(filtered, appId);
  if (byAppId.length > 0) return byAppId;
  const byNull = buildTreeFromNodes(filtered, null);
  if (byNull.length > 0) return byNull;
  return filtered.map((n) => nodeToTreeNode(n, 0, []));
}

/**
 * Merges children under `parentId` in whichever section holds it. A folder's
 * own sharing status says nothing about its collection's section (the API
 * sends none for folders), so guessing the section from the folder misses
 * every folder inside a shared collection.
 */
export function mergeChildrenIntoSections(
  tree: CategorizedNodes,
  parentId: string,
  children: KnowledgeHubNode[],
  effectiveHasChildFolders?: boolean
): CategorizedNodes {
  return {
    shared: mergeChildrenIntoTree(tree.shared, parentId, children, effectiveHasChildFolders),
    private: mergeChildrenIntoTree(tree.private, parentId, children, effectiveHasChildFolders),
  };
}

/**
 * Reattaches the cached children of every open node, walking down from the
 * roots so a folder inside a folder is restored too, in whichever section its
 * collection sits.
 */
export function withOpenFoldersRestored(
  tree: EnhancedFolderTreeNode[],
  childrenCache: Map<string, KnowledgeHubNode[]>,
  expandedFolders: Record<string, boolean>,
): EnhancedFolderTreeNode[] {
  return tree.map((node) => {
    const cached = childrenCache.get(node.id);
    const shown = new Map((node.children as EnhancedFolderTreeNode[]).map((child) => [child.id, child]));
    // Fresh rows from the cache, but keep whatever each row already shows
    // beneath it: a folder may be mid-expand and not yet marked open.
    const children =
      expandedFolders[node.id] && cached && cached.length > 0
        ? cached.map((child) => {
            const fresh = nodeToTreeNode(child, node.depth + 1);
            const existing = shown.get(child.id);
            return existing && existing.children.length > 0
              ? { ...fresh, children: existing.children, hasChildren: existing.hasChildren }
              : fresh;
          })
        : (node.children as EnhancedFolderTreeNode[]);
    return children.length > 0
      ? { ...node, children: withOpenFoldersRestored(children, childrenCache, expandedFolders) }
      : node;
  });
}

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
