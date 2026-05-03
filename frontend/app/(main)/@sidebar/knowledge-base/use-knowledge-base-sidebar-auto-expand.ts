'use client';

import { useEffect, useRef, useState } from 'react';
import type { ReadonlyURLSearchParams } from 'next/navigation';
import { useKnowledgeBaseStore } from '../../knowledge-base/store';
import { treeHasNodeWithId, findAncestorChainIds } from '../../knowledge-base/utils/tree-builder';
import type {
  NodeType,
  EnhancedFolderTreeNode,
  KnowledgeHubNode,
  CategorizedNodes,
  KnowledgeHubApiResponse,
  AllRecordsSidebarSelection,
} from '../../knowledge-base/types';

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

export interface UseKnowledgeBaseSidebarAutoExpandParams {
  searchParams: ReadonlyURLSearchParams;
  isAllRecordsMode: boolean;
  categorizedNodes: CategorizedNodes | null;
  tableData: KnowledgeHubApiResponse | null;
  allRecordsTableData: KnowledgeHubApiResponse | null;
  appNodes: KnowledgeHubNode[];
  appChildrenCache: Map<string, KnowledgeHubNode[]>;
  connectorAppTrees: Map<string, EnhancedFolderTreeNode[]>;
  /** Omitted from the auto-expand effect deps (stable useCallback in parent). */
  handleNodeExpand: (nodeId: string, nodeType: NodeType) => Promise<void>;
  setCurrentFolderId: (folderId: string | null) => void;
  setAllRecordsSidebarSelection: (selection: AllRecordsSidebarSelection) => void;
}

export function useKnowledgeBaseSidebarAutoExpand({
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
}: UseKnowledgeBaseSidebarAutoExpandParams): { isAutoExpanding: boolean } {
  const lastCompletedExpansionKeyRef = useRef<string | null>(null);
  const expansionAttemptGenerationRef = useRef(0);
  const inFlightExpansionKeyRef = useRef<string | null>(null);
  const prevAllRecordsNodeIdRef = useRef<string | null | undefined>(undefined);
  const [isAutoExpanding, setIsAutoExpanding] = useState(false);

  /* Deps mirror page.tsx: handleNodeExpand / setters omitted — expand callback stable; setters from Zustand. */
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

    // KB collection root: prefer id match to `categorizedNodes` roots (API may use folder/recordGroup, not `kb`).
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

  return { isAutoExpanding };
}
