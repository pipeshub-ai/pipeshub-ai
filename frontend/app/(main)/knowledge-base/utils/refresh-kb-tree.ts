import { useKnowledgeBaseStore } from '../store';
import { isKbCollectionsHubApp } from './all-records-transformer';
import {
  collectionsFirst,
  fetchRootAppPage,
  rootListPaginationAfter,
  runReplacingRootListLoad,
  showCollectionsInSidebar,
} from './root-app-list';
import type { KnowledgeHubApiResponse, KnowledgeHubNode } from '../types';

/** Bounds the walk; past it, the sidebar's own "load more" carries on. */
const MAX_ROOT_PAGES_FOR_COLLECTIONS = 50;

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
  await runReplacingRootListLoad(async (isCurrent) => {
    // Root apps of every kind share one list sorted by recent update, so
    // collections can sit on any page behind connectors. Read every page before
    // touching the store: a page that fails part-way must not leave `appNodes`
    // and the tree describing different lists. Always re-fetch — stale in-memory
    // data (e.g. a KB that was just renamed) must not be reused.
    const appItems: KnowledgeHubNode[] = [];
    let pagination: KnowledgeHubApiResponse['pagination'] | undefined;
    let page = 0;
    do {
      page += 1;
      let response: KnowledgeHubApiResponse;
      try {
        response = await fetchRootAppPage(page);
      } catch (error) {
        if (!isCurrent()) return;
        throw error;
      }
      if (!isCurrent()) return;
      appItems.push(...response.items.filter((n) => n.nodeType === 'app'));
      pagination = response.pagination;
    } while (pagination?.hasNext && page < MAX_ROOT_PAGES_FOR_COLLECTIONS);

    const { setAppNodes, setAppRootListPagination } = useKnowledgeBaseStore.getState();
    setAppNodes(collectionsFirst(appItems));
    setAppRootListPagination(rootListPaginationAfter(pagination));
    showCollectionsInSidebar(appItems.filter((n) => isKbCollectionsHubApp(n)));
    afterRefresh?.();
  });
}
