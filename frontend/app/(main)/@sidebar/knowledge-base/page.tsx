'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import KnowledgeBaseSidebar from '../../knowledge-base/sidebar';
import { useKnowledgeBaseStore } from '../../knowledge-base/store';
import { KnowledgeBaseApi } from '../../knowledge-base/api';
import { ADMIN_MORE_CONNECTORS, PERSONAL_MORE_CONNECTORS } from '../../knowledge-base/constants';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { useKnowledgeBaseSidebarAutoExpand } from './use-knowledge-base-sidebar-auto-expand';
import { refreshKbTree } from '../../knowledge-base/utils/refresh-kb-tree';
import { openFolderChildren, reloadOpenFoldersUnder } from '../../knowledge-base/utils/folder-children';
// Registers the sign-out reset for the knowledge base's cached state.
import '../../knowledge-base/utils/sidebar-session';
import { fetchAppDirectChildren } from '../../knowledge-base/utils/fetch-app-direct-children';
import { buildNavUrl, getIsAllRecordsMode } from '../../knowledge-base/utils/nav';
import { findNodeInCategorized } from '../../knowledge-base/utils/find-node';
import { useCallback, useMemo, Suspense } from 'react';
import { toast } from '@/lib/store/toast-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import type { NodeType, EnhancedFolderTreeNode } from '../../knowledge-base/types';
import { useUserPermission, usePermissionDeniedDialog } from '@/config';

function KnowledgeBaseSidebarSlotContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAllRecordsMode = getIsAllRecordsMode(searchParams);
  const canCreateCollection = useUserPermission('createCollection');
  const canDeleteCollection = useUserPermission('deleteCollection');
  const permissionDenied = usePermissionDeniedDialog();

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
    setCurrentFolderId,
    setAllRecordsSidebarSelection,
    setPendingSidebarAction,
  } = useKnowledgeBaseStore();

  const kbApp = useMemo(() => appNodes.find((n) => n.connector === 'KB'), [appNodes]);
  const isSidebarTreeLoading = isAllRecordsMode
    ? isLoadingFlatCollections
    : isLoadingFlatCollections || (kbApp ? loadingAppIds.has(kbApp.id) : false);

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
      // All Records lists an app's children in its own connector tree. In the
      // Collections tree a collection is a folder like any other, loaded below.
      if (nodeType === 'app' && isAllRecordsMode) {
        try {
          await fetchAppDirectChildren(nodeId);
        } catch (error) {
          console.error('Failed to expand app', { nodeId, error });
        }
        return;
      }

      const { categorizedNodes: freshCategorized, connectorAppTrees: freshConnectorTrees } =
        useKnowledgeBaseStore.getState();

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

      try {
        setNodeLoading(nodeId, true);
        await openFolderChildren(nodeId, nodeType);
      } catch (error) {
        console.error('Failed to expand node', { nodeId, error });
      } finally {
        setNodeLoading(nodeId, false);
      }
    },
    [setNodeLoading, isAllRecordsMode]
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
    loadingAppIds,
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
      router.push(buildNavUrl(isAllRecordsMode, { nodeType: 'app', nodeId: id }));
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

  const handleSidebarReindex = useCallback(
    (
      nodeId: string,
      nodeType: NodeType,
      name: string,
      statusFilters?: string[],
      indexingStatus?: string | null,
      hasChildren?: boolean,
    ) => {
      const state = useKnowledgeBaseStore.getState();
      const { node } = findNodeInCategorized(state.categorizedNodes, nodeId);
      const appNode = state.appNodes.find((n) => n.id === nodeId);
      setPendingSidebarAction({
        type: 'reindex',
        nodeId,
        nodeName: node?.name ?? name,
        nodeType: node?.nodeType ?? nodeType,
        statusFilters,
        indexingStatus: node?.indexingStatus ?? indexingStatus,
        hasChildren: hasChildren ?? node?.hasDescendants ?? node?.hasChildren,
        connector: node?.connector ?? appNode?.connector,
        subType: node?.subType ?? appNode?.subType,
      });
    },
    [setPendingSidebarAction]
  );

  const handleSidebarRename = useCallback(async (nodeId: string, newName: string) => {
    const state = useKnowledgeBaseStore.getState();
    const { node, rootKbId } = findNodeInCategorized(state.categorizedNodes, nodeId);
    const kind = node?.nodeType === 'folder' ? 'folder' : 'collection';
    try {
      await KnowledgeBaseApi.renameNode({
        nodeId,
        newName,
        nodeType: node?.nodeType,
        rootKbId: rootKbId ?? undefined,
      });
    } catch (error: unknown) {
      const httpError = error as { response?: { data?: { message?: string } }; message?: string };
      toast.error(httpError?.response?.data?.message || 'Failed to rename');
      throw error;
    }
    toast.success(kind === 'folder' ? 'Folder renamed successfully' : 'Collection renamed successfully');

    // Reload in place rather than clearing the caches first: the reload walks
    // the cached tree to find every open folder that may show the old name.
    const breadcrumbIds = useKnowledgeBaseStore.getState().tableData?.breadcrumbs?.map((bc) => bc.id) ?? [];
    const roots = [...(rootKbId ? [rootKbId] : []), nodeId, ...breadcrumbIds];

    try {
      await refreshKbTree();
      await reloadOpenFoldersUnder(roots, nodeId);
    } catch (error: unknown) {
      console.error('Failed to refresh after rename:', error);
      toast.warning("Couldn't update the list", {
        description: `The ${kind} was renamed, but the list didn't refresh. Refresh the page to see the latest list.`,
      });
    }
  }, []);

  const handleSidebarDelete = useCallback((nodeId: string) => {
    if (!canDeleteCollection) return;
    const state = useKnowledgeBaseStore.getState();
    const { node, rootKbId } = findNodeInCategorized(state.categorizedNodes, nodeId);
    setPendingSidebarAction({
      type: 'delete',
      nodeId,
      nodeName: node?.name ?? nodeId,
      nodeType: node?.nodeType,
      rootKbId: rootKbId ?? undefined,
    });
  }, [setPendingSidebarAction, canDeleteCollection]);

  const handleAddPrivateCollection = useCallback(() => {
    setPendingSidebarAction({ type: 'create-collection' });
  }, [setPendingSidebarAction]);

  return (
    <>
    <KnowledgeBaseSidebar
      pageViewMode={pageViewMode}
      onBack={handleBack}
      sharedTree={categorizedNodes?.shared}
      privateTree={categorizedNodes?.private}
      onSelectKb={handleSelectKb}
      onAddPrivate={permissionDenied.guard(canCreateCollection, handleAddPrivateCollection)}
      onNodeExpand={handleNodeExpand}
      onNodeSelect={handleNodeSelect}
      isLoadingNodes={isSidebarTreeLoading || isAutoExpanding}
      loadingNodeIds={loadingNodeIds}
      appNodes={appNodes}
      appChildrenCache={appChildrenCache}
      connectorAppTrees={connectorAppTrees}
      loadingAppIds={loadingAppIds}
      connectors={storeConnectors}
      moreConnectors={isAdmin === true ? ADMIN_MORE_CONNECTORS : PERSONAL_MORE_CONNECTORS}
      onSidebarReindex={handleSidebarReindex}
      onSidebarRename={handleSidebarRename}
      onSidebarDelete={handleSidebarDelete}
      onAllRecordsSelectAll={handleAllRecordsSelectAll}
      onAllRecordsSelectCollection={handleAllRecordsSelectCollection}
      onAllRecordsSelectConnectorItem={handleAllRecordsSelectConnectorItem}
      onAllRecordsSelectApp={handleAllRecordsSelectApp}
    />
    {permissionDenied.dialog}
    </>
  );
}

export default function KnowledgeBaseSidebarSlot() {
  return (
    <Suspense>
      <KnowledgeBaseSidebarSlotContent />
    </Suspense>
  );
}
