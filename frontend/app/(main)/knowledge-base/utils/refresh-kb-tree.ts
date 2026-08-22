import { useKnowledgeBaseStore } from '../store';
import { KnowledgeHubApi } from '../api';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import { categorizeNodes } from './tree-builder';
import { isKbCollectionsHubApp } from './all-records-transformer';

/**
 * Refreshes the Collections sidebar tree by re-fetching root KB apps.
 *
 * Each KB is now a standalone root-level app — the sidebar tree is built
 * directly from KB apps (categorized into Shared/Private by sharingStatus).
 *
 * Re-merges any previously-expanded folder's cached children back into the
 * rebuilt tree automatically, so expanded state survives a refresh triggered
 * from elsewhere on the page (rename/delete/create-folder). `afterRefresh` is
 * for additional caller-specific work after that merge, not for opting into it.
 */
export async function refreshKbTree(afterRefresh?: () => void): Promise<void> {
  const {
    appNodes,
    setNodes,
    setCategorizedNodes,
    setAppNodes,
    setAppRootListPagination,
    reMergeCachedChildrenIntoTree,
  } = useKnowledgeBaseStore.getState();

  // Always re-fetch root app nodes from the API — this is a "refresh", so
  // stale in-memory data (e.g. a KB that was just renamed) must not be reused.
  const response = await KnowledgeHubApi.getNavigationNodes({
    page: 1,
    limit: SIDEBAR_PAGINATION_PAGE_SIZE,
    include: 'counts',
    sortBy: 'updatedAt',
    sortOrder: 'desc',
  });
  const appItems = response.items.filter((n) => n.nodeType === 'app');
  const freshKbApps = appItems.filter((n) => isKbCollectionsHubApp(n));
  const connectorApps = appItems.filter((n) => !isKbCollectionsHubApp(n));
  setAppNodes([...freshKbApps, ...connectorApps]);
  const p = response.pagination;
  setAppRootListPagination(
    p
      ? {
          hasNext: p.hasNext,
          nextPage: p.hasNext ? p.page + 1 : p.page,
        }
      : null
  );

  let kbApps = freshKbApps;
  if (kbApps.length === 0) {
    // Fetch may have raced with pagination state; fall back to whatever the
    // store now holds rather than silently leaving the sidebar unrefreshed.
    kbApps = appNodes.filter((n) => isKbCollectionsHubApp(n));
    if (kbApps.length === 0) {
      return;
    }
  }

  setNodes(kbApps);
  setCategorizedNodes(categorizeNodes(kbApps, null));
  reMergeCachedChildrenIntoTree();
  afterRefresh?.();
}
