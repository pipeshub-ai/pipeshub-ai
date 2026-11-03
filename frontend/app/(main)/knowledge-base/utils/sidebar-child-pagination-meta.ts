import type { NodeType } from '../types';

type HubPagination = {
  hasNext: boolean;
  nextCursor?: string | null;
};

export type SidebarNodeChildrenPaginationMeta = {
  hasNext: boolean;
  /** Where the next "load more" resumes; null when there is nothing more. */
  nextCursor: string | null;
  nodeType: NodeType;
  /** Pages the list holds, so a reload can re-read the same rows by cursor. */
  pagesLoaded: number;
};

/**
 * Sidebar "load more" state from a response.
 *
 * Only forward paging exists here — the tree appends, it never goes back — so a
 * single `nextCursor` is the whole state. Without one there is no way to ask
 * for more, so `hasNext` is false regardless of what the flag said: offering a
 * "load more" that cannot be satisfied is worse than not offering it.
 */
export function sidebarNodeChildrenMetaFromResponse(
  pagination: HubPagination | undefined,
  nodeType: NodeType,
  pagesLoaded = 1,
): SidebarNodeChildrenPaginationMeta {
  const nextCursor = pagination?.nextCursor ?? null;
  return {
    hasNext: Boolean(pagination?.hasNext && nextCursor),
    nextCursor,
    nodeType,
    pagesLoaded,
  };
}
