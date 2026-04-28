import { KnowledgeHubApi } from '../api';
import { toast } from '@/lib/store/toast-store';
import type { KnowledgeHubNode } from '../types';

/** Caps refresh pagination if the API returns a stuck `hasNext` (defensive). */
const MAX_KB_HUB_CHILD_CURSOR_STEPS = 200;

/**
 * Larger page size for bulk hub refresh only — fewer round-trips than sidebar
 * `load more` while still bounded by {@link MAX_KB_HUB_CHILD_CURSOR_STEPS}.
 */
const KB_HUB_BULK_FETCH_LIMIT = 100;

/**
 * Fetches every cursor page of direct container children for the KB Collections hub app
 * (`parentType` app). Used so the Collections sidebar lists all knowledge bases.
 */
export async function fetchAllKbAppContainerChildren(appId: string): Promise<KnowledgeHubNode[]> {
  const mergedItems: KnowledgeHubNode[] = [];
  let cursor: string | null = null;
  let steps = 0;
  let hasNext = true;

  while (hasNext) {
    steps += 1;
    if (steps > MAX_KB_HUB_CHILD_CURSOR_STEPS) {
      console.error('[fetchAllKbAppContainerChildren] stopped at max cursor steps', {
        appId,
        maxSteps: MAX_KB_HUB_CHILD_CURSOR_STEPS,
        itemsLoaded: mergedItems.length,
      });
      toast.error('Collections list was truncated', {
        description: `Loaded ${mergedItems.length} items; contact support if this persists.`,
      });
      break;
    }

    const response = await KnowledgeHubApi.getNodeChildren(
      'app',
      appId,
      cursor
        ? { onlyContainers: true, cursor }
        : {
            onlyContainers: true,
            limit: KB_HUB_BULK_FETCH_LIMIT,
            sortBy: 'name',
            sortOrder: 'asc',
          }
    );
    mergedItems.push(...response.items);

    const p = response.pagination;
    const next = p?.nextCursor ?? null;
    hasNext = Boolean(p?.hasNext && next);
    if (hasNext && response.items.length === 0) {
      console.error('[fetchAllKbAppContainerChildren] hasNext with empty page; stopping', { appId, steps });
      break;
    }
    cursor = next;
  }
  return mergedItems;
}
