'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import KnowledgeBaseSidebar from '../../knowledge-base/sidebar';
import { useKnowledgeBaseStore } from '../../knowledge-base/store';
import { KnowledgeHubApi, KnowledgeBaseApi } from '../../knowledge-base/api';
import { ADMIN_MORE_CONNECTORS, PERSONAL_MORE_CONNECTORS } from '../../knowledge-base/constants';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import {
  categorizeNode,
  mergeChildrenIntoTree,
  treeHasNodeWithId,
} from '../../knowledge-base/utils/tree-builder';
import { useKnowledgeBaseSidebarAutoExpand } from './use-knowledge-base-sidebar-auto-expand';
import { refreshKbTree } from '../../knowledge-base/utils/refresh-kb-tree';
import { buildNavUrl, getIsAllRecordsMode } from '../../knowledge-base/utils/nav';
import { findNodeInCategorized } from '../../knowledge-base/utils/find-node';
import { useCallback, useMemo, Suspense } from 'react';
import { toast } from '@/lib/store/toast-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import type { NodeType, EnhancedFolderTreeNode, KnowledgeHubNode } from '../../knowledge-base/types';

function KnowledgeBaseSidebarSlotContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAllRecordsMode = getIsAllRecordsMode(searchParams);

  const isAdmin = useUserStore(selectIsAdmin);
  const isMobile = useIsMobile();
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  const closeOnMobile = useCallback(() => {
    if (isMobile) closeMobileSidebar();
  }, [isMobile, closeMobileSidebar]);

  const {
    categorizedNodes,
    appNodes,
    appChildrenCache,
    connectorAppTrees,
    loadingAppIds,
    connectors: storeConnectors,
    loadingNodeIds,
    isLoadingFlatCollections,
    tableData,
    allRecordsTableData,
    setNodeLoading,
    cacheNodeChildren,
    addNodes,
    setCategorizedNodes,
    mergeConnectorAppTreeChildren,
    setCurrentFolderId,
    setAllRecordsSidebarSelection,
    clearNodeCacheEntries,
    reMergeCachedChildrenIntoTree,
    setPendingSidebarAction,
  } = useKnowledgeBaseStore();

  const kbApp = useMemo(() => appNodes.find((n) => n.connector === 'KB'), [appNodes]);
  const isSidebarTreeLoading = isLoadingFlatCollections || (kbApp ? loadingAppIds.has(kbApp.id) : false);

  const pageViewMode = isAllRecordsMode ? 'all-records' : 'collections';

  const handleBack = useCallback(() => router.push('/chat'), [router]);

  const handleSelectKb = useCallback(
    (id: string) => {
      if (id) {
        router.push(buildNavUrl(isAllRecordsMode, { kbId: id }));
      } else {
        router.push(isAllRecordsMode ? '/knowledge-base?view=all-records' : '/knowledge-base');
      }
      closeOnMobile();
    },
    [router, isAllRecordsMode, closeOnMobile]
  );

  const handleNodeExpand = useCallback(
    async (nodeId: string, nodeType: NodeType) => {
      const {
        categorizedNodes: freshCategorized,
        nodeChildrenCache: freshCache,
        connectorAppTrees: freshConnectorTrees,
      } = useKnowledgeBaseStore.getState();

      const hasChildrenInTree = (tree: EnhancedFolderTreeNode[], targetId: string): boolean => {
        for (const node of tree) {
          if (node.id === targetId) return (node.children?.length ?? 0) > 0;
          if (node.children?.length && hasChildrenInTree(node.children as EnhancedFolderTreeNode[], targetId)) {
            return true;
          }
        }
        return false;
      };

      for (const tree of Array.from(freshConnectorTrees.values())) {
        if (hasChildrenInTree(tree, nodeId)) return;
      }

      if (freshCategorized) {
        const alreadyInKbTree =
          hasChildrenInTree(freshCategorized.shared, nodeId) ||
          hasChildrenInTree(freshCategorized.private, nodeId);
        if (alreadyInKbTree) return;
      }

      const mergeIntoConnectorTrees = (
        children: KnowledgeHubNode[],
        effectiveHasChildFolders?: boolean
      ) => {
        const { connectorAppTrees } = useKnowledgeBaseStore.getState();
        for (const [appId, tree] of Array.from(connectorAppTrees.entries())) {
          if (!treeHasNodeWithId(tree, nodeId)) continue;
          mergeConnectorAppTreeChildren(appId, nodeId, children, effectiveHasChildFolders);
          return;
        }
      };

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
        mergeIntoConnectorTrees(cachedChildren);
        return;
      }

      try {
        setNodeLoading(nodeId, true);
        const response = await KnowledgeHubApi.getNodeChildren(nodeType, nodeId, {
          onlyContainers: true,
          page: 1,
          limit: 50,
          include: 'counts',
        });

        cacheNodeChildren(nodeId, response.items);
        addNodes(response.items);

        const foldersCount =
          response.counts?.items?.find((x) => x.label === 'folders')?.count ?? 0;
        const effectiveHasChildFolders = foldersCount > 0;

        const latest = useKnowledgeBaseStore.getState();
        if (latest.categorizedNodes) {
          const parentNode = latest.nodes.find((n) => n.id === nodeId);
          if (parentNode) {
            const section = categorizeNode(parentNode);

            const updatedTree = mergeChildrenIntoTree(
              latest.categorizedNodes[section],
              nodeId,
              response.items,
              effectiveHasChildFolders
            );
            setCategorizedNodes({ ...latest.categorizedNodes, [section]: updatedTree });
          }
        }

        mergeIntoConnectorTrees(response.items, effectiveHasChildFolders);
      } catch (error) {
        console.error('Failed to expand node', { nodeId, error });
      } finally {
        setNodeLoading(nodeId, false);
      }
    },
    [setNodeLoading, cacheNodeChildren, addNodes, setCategorizedNodes, mergeConnectorAppTreeChildren]
  );

  const { isAutoExpanding } = useKnowledgeBaseSidebarAutoExpand({
    searchParams,
    isAllRecordsMode,
    categorizedNodes,
    tableData,
    allRecordsTableData,
    appNodes,
    appChildrenCache,
    connectorAppTrees,
    handleNodeExpand,
    setCurrentFolderId,
    setAllRecordsSidebarSelection,
  });

  const handleNodeSelect = useCallback(
    (nodeType: string, nodeId: string) => {
      setCurrentFolderId(nodeId);
      if (isAllRecordsMode) {
        setAllRecordsSidebarSelection({ type: 'explorer' });
      }
      router.push(buildNavUrl(isAllRecordsMode, { nodeType, nodeId }));
      closeOnMobile();
    },
    [router, isAllRecordsMode, setCurrentFolderId, setAllRecordsSidebarSelection, closeOnMobile]
  );

  // --- All Records mode handlers ---
  const handleAllRecordsSelectAll = useCallback(() => {
    router.push('/knowledge-base?view=all-records');
    closeOnMobile();
  }, [router, closeOnMobile]);

  const handleAllRecordsSelectCollection = useCallback(
    (id: string) => {
      router.push(buildNavUrl(isAllRecordsMode, { nodeType: 'recordGroup', nodeId: id }));
      closeOnMobile();
    },
    [router, isAllRecordsMode, closeOnMobile]
  );

  const handleAllRecordsSelectConnectorItem = useCallback(
    (nodeType: string, nodeId: string) => {
      router.push(buildNavUrl(isAllRecordsMode, { nodeType, nodeId }));
      closeOnMobile();
    },
    [router, isAllRecordsMode, closeOnMobile]
  );

  const handleAllRecordsSelectApp = useCallback(
    (appId: string) => {
      router.push(buildNavUrl(isAllRecordsMode, { nodeType: 'app', nodeId: appId }));
      closeOnMobile();
    },
    [router, isAllRecordsMode, closeOnMobile]
  );

  const handleSidebarReindex = useCallback((nodeId: string) => {
    const findNodeInfo = (): { name: string; nodeType?: NodeType } => {
      const state = useKnowledgeBaseStore.getState();
      const { node } = findNodeInCategorized(state.categorizedNodes, nodeId);
      if (node) return { name: node.name, nodeType: node.nodeType };
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

      await refreshKbTree(reMergeCachedChildrenIntoTree);
    } catch (error: unknown) {
      const httpError = error as { response?: { data?: { message?: string } }; message?: string };
      toast.error(httpError?.response?.data?.message || 'Failed to rename');
      throw error;
    }
  }, [clearNodeCacheEntries, reMergeCachedChildrenIntoTree]);

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

  const handleAddPrivateCollection = useCallback(() => {
    setPendingSidebarAction({ type: 'create-collection' });
  }, [setPendingSidebarAction]);

  const filteredAppNodes = useMemo(
    () =>
      appNodes.filter((app) => {
        if (loadingAppIds.has(app.id)) return true;
        const children = appChildrenCache.get(app.id);
        return children != null && children.length > 0;
      }),
    [appNodes, appChildrenCache, loadingAppIds]
  );

  return (
    <KnowledgeBaseSidebar
      pageViewMode={pageViewMode}
      onBack={handleBack}
      sharedTree={categorizedNodes?.shared}
      privateTree={categorizedNodes?.private}
      onSelectKb={handleSelectKb}
      onAddPrivate={handleAddPrivateCollection}
      onNodeExpand={handleNodeExpand}
      onNodeSelect={handleNodeSelect}
      isLoadingNodes={isSidebarTreeLoading || isAutoExpanding}
      loadingNodeIds={loadingNodeIds}
      appNodes={filteredAppNodes}
      appChildrenCache={appChildrenCache}
      connectorAppTrees={connectorAppTrees}
      loadingAppIds={loadingAppIds}
      connectors={storeConnectors}
      moreConnectors={isAdmin === true ? ADMIN_MORE_CONNECTORS : PERSONAL_MORE_CONNECTORS}
      onSidebarReindex={handleSidebarReindex}
      onSidebarRename={isAllRecordsMode ? undefined : handleSidebarRename}
      onSidebarDelete={handleSidebarDelete}
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
