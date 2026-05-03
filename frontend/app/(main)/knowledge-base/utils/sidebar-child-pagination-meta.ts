import type { NodeType } from '../types';

type HubPagination = {
  hasNext: boolean;
  page: number;
};

export type SidebarNodeChildrenPaginationMeta = {
  hasNext: boolean;
  nextPage: number;
  nodeType: NodeType;
};

/** After loading page `requestedPage`, compute meta for the next request. */
export function sidebarNodeChildrenMetaAfterPage(
  pagination: HubPagination | undefined,
  itemsLength: number,
  pageLimit: number,
  requestedPage: number,
  nodeType: NodeType
): SidebarNodeChildrenPaginationMeta {
  if (pagination) {
    return {
      hasNext: pagination.hasNext,
      nextPage: pagination.hasNext ? pagination.page + 1 : pagination.page,
      nodeType,
    };
  }
  const fullPage = itemsLength >= pageLimit;
  return {
    hasNext: fullPage,
    nextPage: fullPage ? requestedPage + 1 : requestedPage,
    nodeType,
  };
}

/**
 * First-page sidebar cursor (same rules as {@link sidebarNodeChildrenMetaAfterPage} with
 * `requestedPage === 1`).
 */
export function sidebarNodeChildrenMetaFromResponse(
  pagination: HubPagination | undefined,
  itemsLength: number,
  pageLimit: number,
  nodeType: NodeType
): SidebarNodeChildrenPaginationMeta {
  return sidebarNodeChildrenMetaAfterPage(pagination, itemsLength, pageLimit, 1, nodeType);
}
