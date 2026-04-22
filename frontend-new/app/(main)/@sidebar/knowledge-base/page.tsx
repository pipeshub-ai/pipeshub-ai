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
  findAncestorChainIds,
} from '../../knowledge-base/utils/tree-builder';
import { refreshKbTree } from '../../knowledge-base/utils/refresh-kb-tree';
import { buildNavUrl, getIsAllRecordsMode } from '../../knowledge-base/utils/nav';
import { findNodeInCategorized } from '../../knowledge-base/utils/find-node';
import { useCallback, useMemo, Suspense, useEffect, useRef, useState } from 'react';
import { toast } from '@/lib/store/toast-store';
import type { NodeType, EnhancedFolderTreeNode, KnowledgeHubNode } from '../../knowledge-base/types';

function dedupeBreadcrumbsById<T extends { id: string }>(items: T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const item of items) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    out.push(item);
  }
  return out;
}

function KnowledgeBaseSidebarSlotContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAllRecordsMode = getIsAllRecordsMode(searchParams);

  const isAdmin = useUserStore(selectIsAdmin);

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
    },
    [router, isAllRecordsMode]
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

  const lastCompletedExpansionKeyRef = useRef<string | null>(null);
  const expansionAttemptGenerationRef = useRef(0);
  const inFlightExpansionKeyRef = useRef<string | null>(null);
  const [isAutoExpanding, setIsAutoExpanding] = useState(false);

  useEffect(() => {
    const nodeType = searchParams.get('nodeType');
    const nodeId = searchParams.get('nodeId');
    if (!nodeType || !nodeId) return;

    const breadcrumbs = isAllRecordsMode
      ? allRecordsTableData?.breadcrumbs
      : tableData?.breadcrumbs;
    if (!breadcrumbs?.length) return;

    const allRootNodes = [
      ...(categorizedNodes?.shared ?? []),
      ...(categorizedNodes?.private ?? []),
    ];

    if (isAllRecordsMode) {
      if (appNodes.length === 0) return;
    } else if (!categorizedNodes || allRootNodes.length === 0) {
      return;
    }

    const kbBreadcrumb =
      allRootNodes.length > 0
        ? breadcrumbs.find((b) =>
            isAllRecordsMode
              ? allRootNodes.some((n) => n.id === b.id)
              : allRootNodes.some((n) => n.id === b.id) || b.nodeType === 'kb'
          )
        : null;
    const kbTreeNode = kbBreadcrumb ? allRootNodes.find((n) => n.id === kbBreadcrumb.id) : null;

    const branchTag = kbBreadcrumb ? 'kb' : 'app';
    const breadcrumbPathKey = breadcrumbs.map((b) => b.id).join('/');
    const breadcrumbIdSet = new Set(breadcrumbs.map((b) => b.id));

    const nonKbApps = appNodes.filter((a) => a.connector !== 'KB');
    const appsInBreadcrumbTrail = nonKbApps.filter((a) => breadcrumbIdSet.has(a.id));
    const connectorPrimedKey = (() => {
      if (!isAllRecordsMode || appNodes.length === 0) return '';
      const readyFor = (appId: string) => {
        const cached = appChildrenCache.get(appId);
        const tree = connectorAppTrees.get(appId);
        return (cached?.length ?? 0) > 0 && (tree?.length ?? 0) > 0;
      };
      if (appsInBreadcrumbTrail.length > 0) {
        return `trail:${appsInBreadcrumbTrail.map((a) => (readyFor(a.id) ? '1' : '0')).join('')}`;
      }
      const anyReady = nonKbApps.some((a) => readyFor(a.id));
      return `any:${anyReady ? '1' : '0'}`;
    })();

    const expansionKey = `${branchTag}:${nodeType}:${nodeId}:${breadcrumbPathKey}:cprim:${connectorPrimedKey}`;

    if (lastCompletedExpansionKeyRef.current === expansionKey) return;
    if (inFlightExpansionKeyRef.current === expansionKey) return;

    inFlightExpansionKeyRef.current = expansionKey;
    const attemptGeneration = ++expansionAttemptGenerationRef.current;

    async function doExpansion() {
      setIsAutoExpanding(true);
      try {
        setCurrentFolderId(nodeId);

        if (kbBreadcrumb) {
          if (isAllRecordsMode) {
            const collectionName =
              allRootNodes.find((n) => n.id === kbBreadcrumb.id)?.name ||
              kbBreadcrumb.name ||
              kbBreadcrumb.id;
            setAllRecordsSidebarSelection({ type: 'collection', id: kbBreadcrumb.id, name: collectionName });
          }

          const kbId = kbBreadcrumb.id;
          const kbNodeType = (kbTreeNode?.nodeType ?? kbBreadcrumb.nodeType ?? 'kb') as NodeType;

          useKnowledgeBaseStore.getState().expandFolderExclusive(kbId);
          await handleNodeExpand(kbId, kbNodeType);

          const kbIndex = breadcrumbs.findIndex((b) => b.id === kbId);
          const pathAfterKb = breadcrumbs.slice(kbIndex + 1);
          const intermediates = dedupeBreadcrumbsById(
            pathAfterKb.filter((b) => b.id !== nodeId)
          );

          for (const folder of intermediates) {
            useKnowledgeBaseStore.getState().expandFolderExclusive(folder.id);
            await handleNodeExpand(folder.id, folder.nodeType as NodeType);
          }
        } else if (isAllRecordsMode) {
          setAllRecordsSidebarSelection({ type: 'explorer' });

          const store = useKnowledgeBaseStore.getState();
          const apps = store.appNodes;
          const trees = store.connectorAppTrees;
          const flatNodes = store.nodes;

          let anchorAppId: string | null =
            breadcrumbs.find((b) => apps.some((a) => a.id === b.id) || b.nodeType === 'app')?.id ??
            null;

          if (!anchorAppId) {
            for (const [appId, tree] of Array.from(trees.entries())) {
              if (treeHasNodeWithId(tree, nodeId)) {
                anchorAppId = appId;
                break;
              }
            }
          }

          if (!anchorAppId) {
            const hubNode = flatNodes.find((n) => n.id === nodeId);
            const pid = hubNode?.parentId;
            if (typeof pid === 'string' && pid.startsWith('apps/')) {
              anchorAppId = pid.slice('apps/'.length);
            }
          }

          if (anchorAppId) {
            const appIdx = breadcrumbs.findIndex((b) => b.id === anchorAppId);
            if (appIdx >= 0) {
              const intermediates = dedupeBreadcrumbsById(
                breadcrumbs.slice(appIdx + 1).filter((b) => b.id !== nodeId)
              );
              const appNode = apps.find((a) => a.id === anchorAppId);
              const appNodeType = (appNode?.nodeType ?? 'app') as NodeType;
              useKnowledgeBaseStore.getState().expandFolderExclusive(anchorAppId);
              await handleNodeExpand(anchorAppId, appNodeType);
              for (const anc of intermediates) {
                useKnowledgeBaseStore.getState().expandFolderExclusive(anc.id);
                await handleNodeExpand(anc.id, anc.nodeType as NodeType);
              }
            } else {
              const tree = trees.get(anchorAppId);
              if (tree?.length) {
                const ancestorIds = findAncestorChainIds(tree, nodeId);
                if (ancestorIds?.length) {
                  for (const ancId of ancestorIds) {
                    const nType = (flatNodes.find((n) => n.id === ancId)?.nodeType ?? 'folder') as NodeType;
                    useKnowledgeBaseStore.getState().expandFolderExclusive(ancId);
                    await handleNodeExpand(ancId, nType);
                  }
                }
              }
            }
          }
        }

        if (attemptGeneration !== expansionAttemptGenerationRef.current) return;
        lastCompletedExpansionKeyRef.current = expansionKey;
      } catch (err) {
        console.error('Failed to auto-expand sidebar tree', err);
        if (attemptGeneration === expansionAttemptGenerationRef.current) {
          lastCompletedExpansionKeyRef.current = null;
        }
      } finally {
        if (attemptGeneration === expansionAttemptGenerationRef.current) {
          setIsAutoExpanding(false);
          if (inFlightExpansionKeyRef.current === expansionKey) {
            inFlightExpansionKeyRef.current = null;
          }
        }
      }
    }

    doExpansion();
  }, [
    categorizedNodes,
    allRecordsTableData,
    tableData,
    searchParams,
    isAllRecordsMode,
    appNodes,
    appChildrenCache,
    connectorAppTrees,
  ]);

  const prevAllRecordsNodeIdRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (!isAllRecordsMode) return;
    const nodeId = searchParams.get('nodeId');
    if (nodeId === prevAllRecordsNodeIdRef.current) return;
    prevAllRecordsNodeIdRef.current = nodeId;
    if (!nodeId) {
      setCurrentFolderId(null);
      setAllRecordsSidebarSelection({ type: 'all' });
      lastCompletedExpansionKeyRef.current = null;
      inFlightExpansionKeyRef.current = null;
      expansionAttemptGenerationRef.current += 1;
    }
  }, [isAllRecordsMode, searchParams]);

  const handleNodeSelect = useCallback(
    (nodeType: string, nodeId: string) => {
      setCurrentFolderId(nodeId);
      if (isAllRecordsMode) {
        setAllRecordsSidebarSelection({ type: 'explorer' });
      }
      router.push(buildNavUrl(isAllRecordsMode, { nodeType, nodeId }));
    },
    [router, isAllRecordsMode, setCurrentFolderId, setAllRecordsSidebarSelection]
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
