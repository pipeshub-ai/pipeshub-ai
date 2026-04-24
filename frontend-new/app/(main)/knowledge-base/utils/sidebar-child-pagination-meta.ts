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

/**
 * Derives sidebar “load more” cursor from hub response (or infers from page fullness).
 */
export function sidebarNodeChildrenMetaFromResponse(
  pagination: HubPagination | undefined,
  itemsLength: number,
  pageLimit: number,
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
    nextPage: fullPage ? 2 : 1,
    nodeType,
  };
}

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
