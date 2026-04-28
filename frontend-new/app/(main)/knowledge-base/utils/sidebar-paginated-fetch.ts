import { KnowledgeHubApi } from '../api';
import { useKnowledgeBaseStore } from '../store';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import { buildConnectorAppSidebarTree, categorizeNodes, treeHasNodeWithId } from './tree-builder';
import { isKbCollectionsHubApp } from './all-records-transformer';
import {
  sidebarChildrenPaginationFromApi,
  sidebarRootPaginationFromApi,
} from './sidebar-child-pagination-meta';
import { toast } from '@/lib/store/toast-store';
import type { KnowledgeHubNode } from '../types';

function mergeNodesById(existing: KnowledgeHubNode[], incoming: KnowledgeHubNode[]): KnowledgeHubNode[] {
  const byId = new Map(existing.map((n) => [n.id, n]));
  for (const n of incoming) {
    byId.set(n.id, n);
  }
  return Array.from(byId.values());
}

/**
 * Fetches the next cursor page of root apps and appends to the store.
 */
export async function loadMoreRootAppList(): Promise<void> {
  const state = useKnowledgeBaseStore.getState();
  const meta = state.appRootListPagination;
  if (!meta?.hasNext || !meta.nextCursor) return;

  const {
    appendAppNodes,
    setAppRootListPagination,
    setLoadingRootAppListMore,
  } = useKnowledgeBaseStore.getState();

  setLoadingRootAppListMore(true);
  try {
    const response = await KnowledgeHubApi.getNavigationNodes({
      cursor: meta.nextCursor,
      include: 'counts',
    });

    const appItems = response.items.filter((n) => n.nodeType === 'app');
    appendAppNodes(appItems);

    setAppRootListPagination(sidebarRootPaginationFromApi(response.pagination ?? null));
  } catch (error) {
    console.error('loadMoreRootAppList failed:', error);
    toast.error('Could not load more connectors', {
      description: 'Please try again or refresh the page.',
    });
  } finally {
    setLoadingRootAppListMore(false);
  }
}

/**
 * Fetches the next cursor page of direct children for an app and merges into cache and trees.
 */
export async function loadMoreAppChildPage(appId: string): Promise<void> {
  const state = useKnowledgeBaseStore.getState();
  const childMeta = state.appChildrenPagination.get(appId);
  if (!childMeta?.hasNext || !childMeta.nextCursor) return;

  const app = state.appNodes.find((a) => a.id === appId);
  if (!app) return;

  const {
    cacheAppChildren,
    setAppChildPagination,
    setAppLoading,
    setNodes,
    setCategorizedNodes,
    setConnectorAppTree,
    addNodes,
    reMergeCachedChildrenIntoTree,
  } = useKnowledgeBaseStore.getState();

  setAppLoading(appId, true);
  try {
    const response = await KnowledgeHubApi.getNodeChildren('app', appId, {
      onlyContainers: true,
      cursor: childMeta.nextCursor,
    });

    const previous = useKnowledgeBaseStore.getState().appChildrenCache.get(appId) || [];
    const merged = mergeNodesById(previous, response.items);
    cacheAppChildren(appId, merged);

    const p = response.pagination;
    setAppChildPagination(appId, {
      hasNext: Boolean(p?.hasNext && p?.nextCursor),
      nextCursor: p?.nextCursor ?? null,
    });

    const isKbApp = isKbCollectionsHubApp(app);
    if (isKbApp) {
      setNodes(merged);
      const { nodeChildrenCache: freshNodeChildren, addNodes: addNodesFresh } =
        useKnowledgeBaseStore.getState();
      const cachedSubfolderNodes = Array.from(freshNodeChildren.values()).flat();
      if (cachedSubfolderNodes.length > 0) {
        addNodesFresh(cachedSubfolderNodes);
      }
      setCategorizedNodes(categorizeNodes(merged, `apps/${appId}`));
      reMergeCachedChildrenIntoTree();
    } else {
      addNodes(response.items);
      setConnectorAppTree(appId, buildConnectorAppSidebarTree(appId, merged));
    }
  } catch (error) {
    console.error('loadMoreAppChildPage failed:', { appId, error });
    toast.error('Could not load more items', {
      description: 'Please try again or refresh the page.',
    });
  } finally {
    setAppLoading(appId, false);
  }
}

/**
 * Fetches the next cursor page of children for a nested sidebar parent (folder, kb,
 * recordGroup, …). App direct children use {@link loadMoreAppChildPage} instead.
 */
export async function loadMoreNodeChildrenPage(parentId: string): Promise<void> {
  const state = useKnowledgeBaseStore.getState();
  const meta = state.nodeChildrenPagination.get(parentId);
  if (!meta?.hasNext || !meta.nextCursor || meta.nodeType === 'app') return;
  if (state.loadingNodeChildrenMoreIds.has(parentId)) return;

  const {
    cacheNodeChildren,
    setNodeChildrenPagination,
    setLoadingNodeChildrenMore,
    addNodes,
    mergeConnectorAppTreeChildren,
    reMergeCachedChildrenIntoTree,
  } = useKnowledgeBaseStore.getState();

  setLoadingNodeChildrenMore(parentId, true);
  try {
    const response = await KnowledgeHubApi.getNodeChildren(meta.nodeType, parentId, {
      onlyContainers: true,
      cursor: meta.nextCursor,
    });

    const previous = useKnowledgeBaseStore.getState().nodeChildrenCache.get(parentId) || [];
    const merged = mergeNodesById(previous, response.items);
    cacheNodeChildren(parentId, merged);

    setNodeChildrenPagination(parentId, sidebarChildrenPaginationFromApi(response.pagination, meta.nodeType));

    addNodes(response.items);
    reMergeCachedChildrenIntoTree();

    const { connectorAppTrees } = useKnowledgeBaseStore.getState();
    for (const [appId, tree] of connectorAppTrees) {
      if (!treeHasNodeWithId(tree, parentId)) continue;
      mergeConnectorAppTreeChildren(appId, parentId, merged);
      break;
    }
  } catch (error) {
    console.error('loadMoreNodeChildrenPage failed:', { parentId, error });
    toast.error('Could not load more items', {
      description: 'Please try again or refresh the page.',
    });
  } finally {
    setLoadingNodeChildrenMore(parentId, false);
  }
}
