import type { KnowledgeHubApiResponse, NodeType } from '../types';

/** Sidebar “load more” uses hub cursor pagination only (no page / client heuristics). */
export type SidebarNodeChildrenPaginationMeta = {
  hasNext: boolean;
  /** Opaque token for the next `getNodeChildren` request; null when no further pages. */
  nextCursor: string | null;
  nodeType: NodeType;
};

export type SidebarHubCursorPaginationMeta = {
  hasNext: boolean;
  nextCursor: string | null;
};

/**
 * Build sidebar child pagination meta from the hub API `pagination` object.
 * When pagination is missing, treats as no further pages (no inferred hasNext from item counts).
 */
export function sidebarChildrenPaginationFromApi(
  pagination: KnowledgeHubApiResponse['pagination'] | undefined | null,
  nodeType: NodeType
): SidebarNodeChildrenPaginationMeta {
  if (!pagination) {
    return { hasNext: false, nextCursor: null, nodeType };
  }
  const next = pagination.nextCursor ?? null;
  return {
    hasNext: Boolean(pagination.hasNext && next),
    nextCursor: next,
    nodeType,
  };
}

/** Root navigation list (`/knowledge-hub/nodes`) — cursor only. */
export function sidebarRootPaginationFromApi(
  pagination: KnowledgeHubApiResponse['pagination'] | undefined | null
): SidebarHubCursorPaginationMeta {
  if (!pagination) {
    return { hasNext: false, nextCursor: null };
  }
  const next = pagination.nextCursor ?? null;
  return {
    hasNext: Boolean(pagination.hasNext && next),
    nextCursor: next,
  };
}
