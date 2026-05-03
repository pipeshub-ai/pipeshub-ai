import { KnowledgeHubApi } from '../api';
import { useKnowledgeBaseStore } from '../store';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import { buildConnectorAppSidebarTree, categorizeNodes, treeHasNodeWithId } from './tree-builder';
import { isKbCollectionsHubApp } from './all-records-transformer';
import { sidebarNodeChildrenMetaAfterPage } from './sidebar-child-pagination-meta';
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
 * Fetches the next page of root apps and appends to the store.
 */
export async function loadMoreRootAppList(): Promise<void> {
  const state = useKnowledgeBaseStore.getState();
  const meta = state.appRootListPagination;
  if (!meta?.hasNext) return;

  const {
    appendAppNodes,
    setAppRootListPagination,
    setLoadingRootAppListMore,
  } = useKnowledgeBaseStore.getState();

  setLoadingRootAppListMore(true);
  try {
    const response = await KnowledgeHubApi.getNavigationNodes({
      page: meta.nextPage,
      limit: SIDEBAR_PAGINATION_PAGE_SIZE,
      include: 'counts',
      sortBy: 'updatedAt',
      sortOrder: 'desc',
    });

    const appItems = response.items.filter((n) => n.nodeType === 'app');
    appendAppNodes(appItems);

    const p = response.pagination;
    setAppRootListPagination(
      p
        ? {
            hasNext: p.hasNext,
            nextPage: p.hasNext ? p.page + 1 : p.page,
          }
        : null
    );
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
 * Fetches the next page of direct children for an app and merges into cache and trees.
 */
export async function loadMoreAppChildPage(appId: string): Promise<void> {
  const state = useKnowledgeBaseStore.getState();
  const childMeta = state.appChildrenPagination.get(appId);
  if (!childMeta?.hasNext) return;

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
      page: childMeta.nextPage,
      limit: SIDEBAR_PAGINATION_PAGE_SIZE,
      sortBy: 'name',
      sortOrder: 'asc',
    });

    const previous = useKnowledgeBaseStore.getState().appChildrenCache.get(appId) || [];
    const merged = mergeNodesById(previous, response.items);
    cacheAppChildren(appId, merged);

    const p = response.pagination;
    setAppChildPagination(
      appId,
      p
        ? {
            hasNext: p.hasNext,
            nextPage: p.hasNext ? p.page + 1 : p.page,
          }
        : { hasNext: false, nextPage: 1 }
    );

    const isKbApp = isKbCollectionsHubApp(app);
    if (isKbApp) {
      // Same as initial app-child fetch: table/sidebar use full merged list
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
 * Fetches the next page of children for a nested sidebar parent (folder, kb,
 * recordGroup, …). App direct children use {@link loadMoreAppChildPage} instead.
 */
export async function loadMoreNodeChildrenPage(parentId: string): Promise<void> {
  const state = useKnowledgeBaseStore.getState();
  const meta = state.nodeChildrenPagination.get(parentId);
  if (!meta?.hasNext || meta.nodeType === 'app') return;
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
      page: meta.nextPage,
      limit: SIDEBAR_PAGINATION_PAGE_SIZE,
      sortBy: 'name',
      sortOrder: 'asc',
    });

    const previous = useKnowledgeBaseStore.getState().nodeChildrenCache.get(parentId) || [];
    const merged = mergeNodesById(previous, response.items);
    cacheNodeChildren(parentId, merged);

    setNodeChildrenPagination(
      parentId,
      sidebarNodeChildrenMetaAfterPage(
        response.pagination,
        response.items.length,
        SIDEBAR_PAGINATION_PAGE_SIZE,
        meta.nextPage,
        meta.nodeType
      )
    );

    addNodes(response.items);
    reMergeCachedChildrenIntoTree();

    const { connectorAppTrees } = useKnowledgeBaseStore.getState();
    for (const [appId, tree] of connectorAppTrees) {
      if (!treeHasNodeWithId(tree, parentId)) continue;
      mergeConnectorAppTreeChildren(appId, parentId, merged);
      // Each hub node appears under at most one connector app tree.
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
