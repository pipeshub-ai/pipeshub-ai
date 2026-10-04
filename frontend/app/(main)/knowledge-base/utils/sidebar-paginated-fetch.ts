import { KnowledgeHubApi } from '../api';
import { useKnowledgeBaseStore } from '../store';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import { buildConnectorAppSidebarTree } from './tree-builder';
import { isKbCollectionsHubApp } from './all-records-transformer';
import {
  fetchRootAppPage,
  isReplacingRootListLoadInFlight,
  restoreOpenFoldersInSidebar,
  rootListPaginationAfter,
  showCollectionsInSidebar,
  watchRootList,
} from './root-app-list';
import { loadNextChildrenPage, showFolderChildren } from './folder-children';
import { kbSessionToken } from './kb-session';
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
  // A refresh or first-page load already reads the pages this would; its
  // result replaces the list, so a page fetched now would only be discarded.
  if (!meta?.hasNext || isReplacingRootListLoadInFlight()) return;

  const { setLoadingRootAppListMore } = state;
  const isCurrent = watchRootList();

  setLoadingRootAppListMore(true);
  try {
    const response = await fetchRootAppPage(meta.nextPage);
    // Stale if a refresh started meanwhile, or one already running when this
    // was clicked has since written its own cursor: its pages replace ours.
    const cursorNow = useKnowledgeBaseStore.getState().appRootListPagination;
    if (
      !isCurrent() ||
      isReplacingRootListLoadInFlight() ||
      !cursorNow?.hasNext ||
      cursorNow.nextPage !== meta.nextPage
    ) {
      return;
    }

    const appItems = response.items.filter((n) => n.nodeType === 'app');
    const { appendAppNodes, setAppRootListPagination, nodes } = useKnowledgeBaseStore.getState();
    appendAppNodes(appItems);
    setAppRootListPagination(rootListPaginationAfter(response.pagination));

    const knownIds = new Set(nodes.map((n) => n.id));
    const newCollections = appItems.filter((n) => isKbCollectionsHubApp(n) && !knownIds.has(n.id));
    if (newCollections.length > 0) {
      showCollectionsInSidebar([...nodes.filter((n) => n.nodeType === 'app'), ...newCollections]);
    }
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
    setConnectorAppTree,
    addNodes,
  } = useKnowledgeBaseStore.getState();

  const stillSignedIn = kbSessionToken();
  setAppLoading(appId, true);
  try {
    const response = await KnowledgeHubApi.getNodeChildren('app', appId, {
      onlyContainers: true,
      page: childMeta.nextPage,
      limit: SIDEBAR_PAGINATION_PAGE_SIZE,
      sortBy: 'name',
      sortOrder: 'asc',
    });
    if (!stillSignedIn()) return;

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

    // Build the tree for BOTH KB and connector apps — see
    // fetch-app-direct-children.ts for why KB apps must not be skipped here.
    addNodes(response.items);
    setConnectorAppTree(appId, buildConnectorAppSidebarTree(appId, merged));
  } catch (error) {
    if (!stillSignedIn()) return;
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
 * recordGroup, or a collection in the Collections tree). All Records app
 * direct children use {@link loadMoreAppChildPage} instead.
 */
export async function loadMoreNodeChildrenPage(parentId: string): Promise<void> {
  const state = useKnowledgeBaseStore.getState();
  if (!state.nodeChildrenPagination.get(parentId)?.hasNext) return;
  if (state.loadingNodeChildrenMoreIds.has(parentId)) return;

  const { setLoadingNodeChildrenMore } = state;
  setLoadingNodeChildrenMore(parentId, true);
  try {
    if (await loadNextChildrenPage(parentId)) {
      showFolderChildren(parentId);
      restoreOpenFoldersInSidebar();
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
