import { KnowledgeHubApi } from '../api';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import type { KnowledgeHubNode } from '../types';

/** Caps refresh pagination if the API returns a stuck `hasNext` (defensive). */
const MAX_KB_HUB_CHILD_PAGES = 200;

/**
 * Fetches every page of direct container children for the KB Collections hub app
 * (`parentType` app). Used so the Collections sidebar lists all knowledge bases,
 * not only the first `SIDEBAR_PAGINATION_PAGE_SIZE` items.
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
      break;
    }
    const response = await KnowledgeHubApi.getNodeChildren('app', appId, {
      onlyContainers: true,
      page,
      limit: SIDEBAR_PAGINATION_PAGE_SIZE,
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
