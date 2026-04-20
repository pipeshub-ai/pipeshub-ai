'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import KnowledgeBaseSidebar from '../../knowledge-base/sidebar';
import { useKnowledgeBaseStore } from '../../knowledge-base/store';
import { KnowledgeHubApi, KnowledgeBaseApi } from '../../knowledge-base/api';
import { MORE_CONNECTORS } from '../../knowledge-base/mock-data';
import { categorizeNode, mergeChildrenIntoTree } from '../../knowledge-base/utils/tree-builder';
import { refreshKbTree } from '../../knowledge-base/utils/refresh-kb-tree';
import { buildNavUrl, getIsAllRecordsMode } from '../../knowledge-base/utils/nav';
import { findNodeInCategorized } from '../../knowledge-base/utils/find-node';
import { useCallback, useMemo, Suspense, useEffect, useRef, useState } from 'react';
import { toast } from '@/lib/store/toast-store';
import type { NodeType, EnhancedFolderTreeNode } from '../../knowledge-base/types';

function KnowledgeBaseSidebarSlotContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAllRecordsMode = getIsAllRecordsMode(searchParams);

  const {
    categorizedNodes,
    appNodes,
    appChildrenCache,
    loadingAppIds,
    connectors: storeConnectors,
    loadingNodeIds,
    isLoadingFlatCollections,
    // Table data (for breadcrumbs used by auto-expansion)
    tableData,
    allRecordsTableData,
    // Store actions
    setNodeLoading,
    cacheNodeChildren,
    addNodes,
    setCategorizedNodes,
    setCurrentFolderId,
    setAllRecordsSidebarSelection,
    clearNodeCacheEntries,
    reMergeCachedChildrenIntoTree,
    setPendingSidebarAction,
  } = useKnowledgeBaseStore();

  // isLoadingNodes: true while the KB app's children (root collections) are being fetched.
  // Covers both the root-nodes fetch AND the KB app-children fetch.
  const kbApp = useMemo(() => appNodes.find((n) => n.connector === 'KB'), [appNodes]);
  // Derive loading state purely from explicit flags so we don't get stuck in a
  // permanent loading state when the fetch fails or returns no KB app.
  const isSidebarTreeLoading = isLoadingFlatCollections || (kbApp ? loadingAppIds.has(kbApp.id) : false);

  // Derive page view mode from URL (single source of truth)
  const pageViewMode = isAllRecordsMode ? 'all-records' : 'collections';

  const handleBack = useCallback(() => router.push('/chat'), [router]);

  // --- Collections mode: KB selection → navigate via URL ---
  const handleSelectKb = useCallback(
    (id: string) => {
      if (id) {
        router.push(buildNavUrl(isAllRecordsMode, { kbId: id }));
      } else {
        router.push(isAllRecordsMode ? '/knowledge-base?view=all-records' : '/knowledge-base');
      }
    },
    [router, isAllRecordsMode]
  );

  // --- Collections mode: Expand node → fetch children from API ---
  const handleNodeExpand = useCallback(
    async (nodeId: string, nodeType: NodeType) => {
      const {
        categorizedNodes: freshCategorized,
        nodeChildrenCache: freshCache,
      } = useKnowledgeBaseStore.getState();

      // Check if children already exist in tree
      const hasChildrenInTree = (tree: EnhancedFolderTreeNode[], targetId: string): boolean => {
        for (const node of tree) {
          if (node.id === targetId) return (node.children?.length ?? 0) > 0;
          if (node.children?.length && hasChildrenInTree(node.children as EnhancedFolderTreeNode[], targetId)) {
            return true;
          }
        }
        return false;
      };

      if (freshCategorized) {
        const alreadyInTree =
          hasChildrenInTree(freshCategorized.shared, nodeId) ||
          hasChildrenInTree(freshCategorized.private, nodeId);
        if (alreadyInTree) return;
      }

      // Check cache — re-merge if stale
      const cachedChildren = freshCache.get(nodeId);
      if (cachedChildren && cachedChildren.length > 0) {
        addNodes(cachedChildren);
        const latest = useKnowledgeBaseStore.getState();
        if (latest.categorizedNodes) {
          const parentNode = latest.nodes.find((n) => n.id === nodeId);
          if (parentNode) {
            const section = categorizeNode(parentNode);
            const updatedTree = mergeChildrenIntoTree(
              latest.categorizedNodes[section],
              nodeId,
              cachedChildren
            );
            setCategorizedNodes({ ...latest.categorizedNodes, [section]: updatedTree });
          }
        }
        return;
      }

      // Fetch from API
      try {
        setNodeLoading(nodeId, true);
        const response = await KnowledgeHubApi.getNodeChildren(nodeType, nodeId, {
          page: 1,
          limit: 50,
          include: 'counts',
        });

        cacheNodeChildren(nodeId, response.items);
        addNodes(response.items);

        const latest = useKnowledgeBaseStore.getState();
        if (latest.categorizedNodes) {
          const parentNode = latest.nodes.find((n) => n.id === nodeId);
          if (parentNode) {
            const section = categorizeNode(parentNode);

            // Always derive effectiveHasChildren from the fresh counts response.
            // The original hasChildren on the node may be stale or wrong (e.g. the
            // server said true but no folder children actually exist). counts is the
            // authoritative answer: if the "folders" entry is absent or 0, this node
            // has no sub-folder children and the chevron should not show.
            const foldersCount =
              response.counts?.items?.find((x) => x.label === 'folders')?.count ?? 0;
            const effectiveHasChildFolders = foldersCount > 0;

            const updatedTree = mergeChildrenIntoTree(
              latest.categorizedNodes[section],
              nodeId,
              response.items,
              effectiveHasChildFolders
            );
            setCategorizedNodes({ ...latest.categorizedNodes, [section]: updatedTree });
          }
        }
      } catch (error) {
        console.error('Failed to expand node', { nodeId, error });
      } finally {
        setNodeLoading(nodeId, false);
      }
    },
    [setNodeLoading, cacheNodeChildren, addNodes, setCategorizedNodes]
  );

  // --- Auto-expansion: open sidebar tree to the currently navigated node ---
  //
  // WHY THIS EXISTS: fetchTableData in page.tsx tries to auto-expand but may
  // run before categorizedNodes is populated (race condition with KB app
  // children loading). This effect watches BOTH categorizedNodes AND tableData
  // and fires when the LAST of the two arrives, so expansion always succeeds.
  //
  // KEY DESIGN DECISIONS:
  // 1. Use ID-based matching to find the KB root breadcrumb — NOT nodeType check.
  //    The API may return 'folder' for all breadcrumb nodeTypes, even the KB root.
  //    Matching against categorizedNodes (which we know are KBs) is reliable.
  // 2. Set lastAutoExpandedNodeIdRef AFTER confirming the KB root is found,
  //    NOT before. This allows retries if the first attempt cannot find the root.
  // 3. Reset the ref on error so broken navigations can recover on re-render.
  const lastAutoExpandedNodeIdRef = useRef<string | null>(null);
  const [isAutoExpanding, setIsAutoExpanding] = useState(false);

  useEffect(() => {
    const nodeType = searchParams.get('nodeType');
    const nodeId = searchParams.get('nodeId');

    const allRootNodes = [
      ...(categorizedNodes?.shared ?? []),
      ...(categorizedNodes?.private ?? []),
    ];

    if (!nodeType || !nodeId) return;
    if (!categorizedNodes || allRootNodes.length === 0) return;

    // Use the appropriate breadcrumbs based on view mode.
    // All Records uses allRecordsTableData; Collections uses tableData.
    const breadcrumbs = isAllRecordsMode
      ? allRecordsTableData?.breadcrumbs
      : tableData?.breadcrumbs;

    if (!breadcrumbs?.length) return;

    // -----------------------------------------------------------------------
    // Detect the KB root breadcrumb.
    //
    // IMPORTANT: Do NOT rely on b.nodeType === 'kb'. The API may return a
    // different nodeType for collection roots (e.g. 'folder' or 'recordGroup').
    // Match by ID against the nodes in categorizedNodes — those came from the
    // KB app's children and are guaranteed to be KB-level nodes.
    // In All Records mode the nodeType in the URL may be 'recordGroup'; the
    // ID-based match still works because KB app children populate categorizedNodes.
    // -----------------------------------------------------------------------
    const kbBreadcrumb = breadcrumbs.find(
      (b) => allRootNodes.some((n) => n.id === b.id) || b.nodeType === 'kb'
    );
    const kbTreeNode = kbBreadcrumb ? allRootNodes.find((n) => n.id === kbBreadcrumb.id) : null;

    console.log('DEBUG::sidebar::autoExpand::kbBreadcrumb', { kbBreadcrumb, allRootNodes: allRootNodes.map(n => ({ id: n.id, name: n.name, nodeType: n.nodeType })) });

    if (!kbBreadcrumb) {
      // Can't expand — KB root not yet in tree or breadcrumbs don't include it.
      // Do NOT mark as expanded; allow retry when categorizedNodes updates.
      return;
    }

    // Avoid re-running for the same target to prevent loops triggered by the
    // setCategorizedNodes calls inside handleNodeExpand.
    if (lastAutoExpandedNodeIdRef.current === nodeId) return;

    // Mark as in-progress NOW (after confirming we have a valid KB root).
    lastAutoExpandedNodeIdRef.current = nodeId;

    async function doExpansion() {
      setIsAutoExpanding(true);
      try {
        // ── Step 0: Highlight the target node in the sidebar ─────────────────
        // setCurrentFolderId drives the isSelected highlight on FolderTreeItem.
        // Must be set here for direct-link navigation (page.tsx only sets it
        // from the old 'folderId' param, not from 'nodeId').
        setCurrentFolderId(nodeId!);

        // In All Records mode, also sync allRecordsSidebarSelection so the
        // "All" button deselects and the correct root collection is reflected.
        // Use the KB ROOT breadcrumb (the collection), not the deep nodeId
        // (which may be a sub-folder). This keeps the collection-level selection
        // consistent while currentFolderId drives the deep FolderTreeItem highlight.
        if (isAllRecordsMode) {
          const collectionName =
            allRootNodes.find((n) => n.id === kbBreadcrumb!.id)?.name ||
            kbBreadcrumb!.name ||
            kbBreadcrumb!.id;
          setAllRecordsSidebarSelection({ type: 'collection', id: kbBreadcrumb!.id, name: collectionName });
        }

        // ── Step 1: Expand the KB root ───────────────────────────────────────
        const kbId = kbBreadcrumb!.id;
        // Use nodeType from categorizedNodes tree node (reliable), fallback to
        // breadcrumb nodeType, then default to 'kb'.
        const kbNodeType = (kbTreeNode?.nodeType ?? kbBreadcrumb!.nodeType ?? 'kb') as NodeType;

        // Exclusive expand collapses sibling KBs so only the target branch is open.
        useKnowledgeBaseStore.getState().expandFolderExclusive(kbId);
        await handleNodeExpand(kbId, kbNodeType);

        // ── Step 2: Expand each folder ancestor between KB root and the target ──
        const kbIndex = breadcrumbs!.findIndex((b) => b.id === kbId);
        const pathAfterKb = breadcrumbs!.slice(kbIndex + 1);

        // We want to expand ancestors but NOT the target node itself:
        // - If nodeId is in breadcrumbs (last entry), exclude it.
        // - If nodeId is the currentNode (not in breadcrumbs), expand all of pathAfterKb.
        const intermediates = pathAfterKb.filter((b) => b.id !== nodeId);

        for (const folder of intermediates) {
          useKnowledgeBaseStore.getState().expandFolderExclusive(folder.id);
          await handleNodeExpand(folder.id, folder.nodeType as NodeType);
        }
      } catch (err) {
        console.error('Failed to auto-expand sidebar tree', err);
        // Reset so the same nodeId can be retried on next render cycle.
        lastAutoExpandedNodeIdRef.current = null;
      } finally {
        setIsAutoExpanding(false);
      }
    }

    doExpansion();
  }, [categorizedNodes, allRecordsTableData, tableData, searchParams, isAllRecordsMode]);
  // NOTE: handleNodeExpand intentionally omitted from deps — it is stable and
  // including it would cause the effect to re-run on every expansion step.

  // All Records mode: when no specific node is selected (root view), reset
  // sidebar selection back to "All" and clear currentFolderId so no tree item
  // remains highlighted from a previous navigation.
  const prevAllRecordsNodeIdRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (!isAllRecordsMode) return;
    const nodeId = searchParams.get('nodeId');
    // Only reset sidebar when nodeId actually changes (e.g. user navigated away
    // from a node back to root), not on every searchParams update (e.g. page
    // number or filter changes). Without this guard, paginating while in root
    // view would reset the sidebar selection and, worse, reset the page to 1
    // via setAllRecordsSidebarSelection's side-effect.
    if (nodeId === prevAllRecordsNodeIdRef.current) return;
    prevAllRecordsNodeIdRef.current = nodeId;
    if (!nodeId) {
      setCurrentFolderId(null);
      setAllRecordsSidebarSelection({ type: 'all' });
      // Reset the ref so future navigation to the same nodeId triggers expansion.
      lastAutoExpandedNodeIdRef.current = null;
    }
  }, [isAllRecordsMode, searchParams]);

  const handleNodeSelect = useCallback(
    (nodeType: string, nodeId: string) => {
      setCurrentFolderId(nodeId);
      router.push(buildNavUrl(isAllRecordsMode, { nodeType, nodeId }));
    },
    [router, isAllRecordsMode, setCurrentFolderId]
  );

  // --- All Records mode handlers ---
  const handleAllRecordsSelectAll = useCallback(() => {
    router.push('/knowledge-base?view=all-records');
  }, [router]);

  const handleAllRecordsSelectCollection = useCallback(
    (id: string) => {
      router.push(buildNavUrl(isAllRecordsMode, { nodeType: 'recordGroup', nodeId: id }));
    },
    [router, isAllRecordsMode]
  );

  const handleAllRecordsSelectConnectorItem = useCallback(
    (nodeType: string, nodeId: string) => {
      router.push(buildNavUrl(isAllRecordsMode, { nodeType, nodeId }));
    },
    [router, isAllRecordsMode]
  );

  const handleAllRecordsSelectApp = useCallback(
    (appId: string) => {
      router.push(buildNavUrl(isAllRecordsMode, { nodeType: 'app', nodeId: appId }));
    },
    [router, isAllRecordsMode]
  );

  // --- Sidebar item actions ---

  /** Reindex: set pending action → page.tsx picks it up and opens dialog */
  const handleSidebarReindex = useCallback((nodeId: string) => {
    const findNodeInfo = (): { name: string; nodeType?: NodeType } => {
      const state = useKnowledgeBaseStore.getState();
      const { node } = findNodeInCategorized(state.categorizedNodes, nodeId);
      if (node) return { name: node.name, nodeType: node.nodeType };
      // Check app nodes / app children cache
      const appNode = state.appNodes.find((n) => n.id === nodeId);
      if (appNode) return { name: appNode.name, nodeType: appNode.nodeType };
      const cacheEntries = Array.from(state.appChildrenCache.values());
      for (const children of cacheEntries) {
        const child = children.find((c) => c.id === nodeId);
        if (child) return { name: child.name, nodeType: child.nodeType };
      }
      return { name: nodeId };
    };
    const nodeInfo = findNodeInfo();
    setPendingSidebarAction({ type: 'reindex', nodeId, nodeName: nodeInfo.name, nodeType: nodeInfo.nodeType });
  }, [setPendingSidebarAction]);

  /** Rename: call API directly (no confirmation dialog needed) */
  const handleSidebarRename = useCallback(async (nodeId: string, newName: string) => {
    try {
      const state = useKnowledgeBaseStore.getState();
      const { node, rootKbId } = findNodeInCategorized(state.categorizedNodes, nodeId);

      await KnowledgeBaseApi.renameNode({
        nodeId,
        newName,
        nodeType: node?.nodeType,
        rootKbId: rootKbId ?? undefined,
      });
      toast.success(
        node?.nodeType === 'folder' ? 'Folder renamed successfully' : 'Collection renamed successfully'
      );

      // Clear stale cache: breadcrumb path + parent of renamed node (so re-expand shows new name)
      const currentState = useKnowledgeBaseStore.getState();
      const cacheIdsToClear: string[] = [];
      if (currentState.tableData?.breadcrumbs) {
        cacheIdsToClear.push(...currentState.tableData.breadcrumbs.map(bc => bc.id));
      }
      if (rootKbId) {
        cacheIdsToClear.push(rootKbId);
      }
      if (cacheIdsToClear.length > 0) {
        clearNodeCacheEntries(cacheIdsToClear);
      }

      // Refresh Collections via the KB app children (unified API call)
      await refreshKbTree(reMergeCachedChildrenIntoTree);
    } catch (error: unknown) {
      const httpError = error as { response?: { data?: { message?: string } }; message?: string };
      toast.error(httpError?.response?.data?.message || 'Failed to rename');
      throw error;
    }
  }, [clearNodeCacheEntries, reMergeCachedChildrenIntoTree]);

  /** Delete: set pending action → page.tsx picks it up and opens dialog */
  const handleSidebarDelete = useCallback((nodeId: string) => {
    const state = useKnowledgeBaseStore.getState();
    const { node, rootKbId } = findNodeInCategorized(state.categorizedNodes, nodeId);
    setPendingSidebarAction({
      type: 'delete',
      nodeId,
      nodeName: node?.name ?? nodeId,
      nodeType: node?.nodeType,
      rootKbId: rootKbId ?? undefined,
    });
  }, [setPendingSidebarAction]);

  /** Add private collection: set pending action → page.tsx picks it up and opens dialog */
  const handleAddPrivateCollection = useCallback(() => {
    setPendingSidebarAction({ type: 'create-collection' });
  }, [setPendingSidebarAction]);

  // Filter appNodes to only show apps that have children loaded
  const filteredAppNodes = useMemo(
    () => appNodes.filter((app) => {
      const children = appChildrenCache.get(app.id);
      return children && children.length > 0;
    }),
    [appNodes, appChildrenCache]
  );

  return (
    <KnowledgeBaseSidebar
      pageViewMode={pageViewMode}
      onBack={handleBack}
      // Collections mode
      sharedTree={categorizedNodes?.shared}
      privateTree={categorizedNodes?.private}
      onSelectKb={handleSelectKb}
      onAddPrivate={handleAddPrivateCollection}
      onNodeExpand={handleNodeExpand}
      onNodeSelect={handleNodeSelect}
      isLoadingNodes={isSidebarTreeLoading || isAutoExpanding}
      loadingNodeIds={loadingNodeIds}
      // All Records mode
      appNodes={filteredAppNodes}
      appChildrenCache={appChildrenCache}
      loadingAppIds={loadingAppIds}
      connectors={storeConnectors}
      moreConnectors={MORE_CONNECTORS}
      // Sidebar item actions
      onSidebarReindex={handleSidebarReindex}
      onSidebarRename={isAllRecordsMode ? undefined : handleSidebarRename}
      onSidebarDelete={handleSidebarDelete}
      // All Records navigation
      onAllRecordsSelectAll={handleAllRecordsSelectAll}
      onAllRecordsSelectCollection={handleAllRecordsSelectCollection}
      onAllRecordsSelectConnectorItem={handleAllRecordsSelectConnectorItem}
      onAllRecordsSelectApp={handleAllRecordsSelectApp}
    />
  );
}

export default function KnowledgeBaseSidebarSlot() {
  return (
    <Suspense>
      <KnowledgeBaseSidebarSlotContent />
    </Suspense>
  );
}
