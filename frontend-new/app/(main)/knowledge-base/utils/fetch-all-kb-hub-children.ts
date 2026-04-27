import { KnowledgeHubApi } from '../api';
import { toast } from '@/lib/store/toast-store';
import type { KnowledgeHubNode } from '../types';

/** Caps refresh pagination if the API returns a stuck `hasNext` (defensive). */
const MAX_KB_HUB_CHILD_PAGES = 200;

/**
 * Larger page size for bulk hub refresh only — fewer round-trips than sidebar
 * `load more` while still bounded by {@link MAX_KB_HUB_CHILD_PAGES}.
 */
const KB_HUB_BULK_FETCH_LIMIT = 100;

/**
 * Fetches every page of direct container children for the KB Collections hub app
 * (`parentType` app). Used so the Collections sidebar lists all knowledge bases,
 * not only the first page of items.
 */
export async function fetchAllKbAppContainerChildren(appId: string): Promise<KnowledgeHubNode[]> {
  const mergedItems: KnowledgeHubNode[] = [];
  let page = 1;
  let hasNext = true;
  while (hasNext) {
    if (page > MAX_KB_HUB_CHILD_PAGES) {
      console.error('[fetchAllKbAppContainerChildren] stopped at max pages', {
        appId,
        maxPages: MAX_KB_HUB_CHILD_PAGES,
        itemsLoaded: mergedItems.length,
      });
      toast.error('Collections list was truncated', {
        description: `Loaded ${mergedItems.length} items; contact support if this persists.`,
      });
      break;
    }
    const response = await KnowledgeHubApi.getNodeChildren('app', appId, {
      onlyContainers: true,
      page,
      limit: KB_HUB_BULK_FETCH_LIMIT,
      sortBy: 'name',
      sortOrder: 'asc',
    });
    mergedItems.push(...response.items);
    hasNext = response.pagination?.hasNext ?? false;
    if (hasNext && response.items.length === 0) {
      console.error('[fetchAllKbAppContainerChildren] hasNext with empty page; stopping', { appId, page });
      break;
    }
    page += 1;
  }
  return mergedItems;
}
