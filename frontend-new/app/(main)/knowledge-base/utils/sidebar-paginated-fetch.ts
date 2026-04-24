import { KnowledgeHubApi } from '../api';
import { useKnowledgeBaseStore } from '../store';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import { buildConnectorAppSidebarTree, categorizeNodes } from './tree-builder';
import { isKbCollectionsHubApp } from './all-records-transformer';
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
