import { KnowledgeHubApi } from '../api';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import type { KnowledgeHubNode } from '../types';

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
    const response = await KnowledgeHubApi.getNodeChildren('app', appId, {
      onlyContainers: true,
      page,
      limit: SIDEBAR_PAGINATION_PAGE_SIZE,
      sortBy: 'name',
      sortOrder: 'asc',
    });
    mergedItems.push(...response.items);
    hasNext = response.pagination?.hasNext ?? false;
    page += 1;
  }
  return mergedItems;
}
