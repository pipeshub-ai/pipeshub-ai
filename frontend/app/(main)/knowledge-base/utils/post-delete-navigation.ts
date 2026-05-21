import type { Breadcrumb } from '../types';

export type PostDeleteNavigation =
  | { kind: 'navigate'; nodeType: string; nodeId: string }
  | { kind: 'root' }
  | { kind: 'none' };

export interface PostDeleteNavigationContext {
  deletedIds: string[];
  breadcrumbs?: Breadcrumb[] | null;
  /** URL `nodeId` in Collections mode */
  urlNodeId?: string | null;
  /** Last loaded table `currentNode.id` */
  currentNodeId?: string | null;
  parentNode?: { id: string; name: string; nodeType: string } | null;
}

export interface HubNotFoundRecoveryContext {
  failedNodeId: string;
  urlNodeId?: string | null;
  tableData?: {
    breadcrumbs?: Breadcrumb[] | null;
    parentNode?: { id: string } | null;
    currentNode?: { id: string } | null;
  } | null;
  /** Node ids from a recent delete — suppress global Not Found toast while URL catches up */
  pendingSilentNotFoundNodeIds?: ReadonlySet<string>;
}

/**
 * True when a 404 on the main table fetch should redirect without the global Not Found toast
 * (post-delete race or stale URL while the user was browsing that folder).
 */
export function shouldSilentlyRecoverHubNotFound(ctx: HubNotFoundRecoveryContext): boolean {
  const { failedNodeId, urlNodeId, tableData, pendingSilentNotFoundNodeIds } = ctx;
  if (pendingSilentNotFoundNodeIds?.has(failedNodeId)) {
    return true;
  }
  const urlPointsAtFailed = Boolean(urlNodeId && urlNodeId === failedNodeId);
  if (!urlPointsAtFailed) {
    return false;
  }
  return Boolean(
    tableData?.breadcrumbs?.length ||
      tableData?.parentNode?.id ||
      tableData?.currentNode?.id === failedNodeId
  );
}

/**
 * Decide where to navigate after nodes were removed (delete) or are missing (404).
 * Collections-mode semantics; All Records callers should skip or pass no-op context.
 */
export function resolvePostDeleteNavigation(ctx: PostDeleteNavigationContext): PostDeleteNavigation {
  const deleted = new Set(ctx.deletedIds.filter(Boolean));
  if (deleted.size === 0) {
    return { kind: 'none' };
  }

  const crumbs = ctx.breadcrumbs;
  if (crumbs?.length) {
    const hitIndices: number[] = [];
    for (let i = 0; i < crumbs.length; i++) {
      if (deleted.has(crumbs[i].id)) {
        hitIndices.push(i);
      }
    }
    if (hitIndices.length > 0) {
      const minIdx = Math.min(...hitIndices);
      if (minIdx > 0) {
        const target = crumbs[minIdx - 1];
        return { kind: 'navigate', nodeType: target.nodeType, nodeId: target.id };
      }
      return { kind: 'root' };
    }
  }

  const urlId = ctx.urlNodeId ?? null;
  const currentId = ctx.currentNodeId ?? null;
  const urlOrCurrentHit =
    (urlId && deleted.has(urlId)) || (currentId && deleted.has(currentId)) || false;

  if (urlOrCurrentHit) {
    const p = ctx.parentNode;
    // Guard: parentNode itself may have been bulk-deleted alongside the current node.
    if (p?.id && p.nodeType && !deleted.has(p.id)) {
      return { kind: 'navigate', nodeType: p.nodeType, nodeId: p.id };
    }
    // Breadcrumbs often omit the current leaf; deepest crumb that is not deleted is a safe parent.
    if (crumbs?.length) {
      for (let i = crumbs.length - 1; i >= 0; i--) {
        if (!deleted.has(crumbs[i].id)) {
          return { kind: 'navigate', nodeType: crumbs[i].nodeType, nodeId: crumbs[i].id };
        }
      }
    }
    return { kind: 'root' };
  }

  return { kind: 'none' };
}

/**
 * When a hub fetch for a node returns 404, derive recovery navigation for that node id.
 */
export function resolveHubNodeNotFoundNavigation(
  failedNodeId: string,
  ctx: Omit<PostDeleteNavigationContext, 'deletedIds'>
): PostDeleteNavigation {
  return resolvePostDeleteNavigation({
    ...ctx,
    deletedIds: [failedNodeId],
    urlNodeId: ctx.urlNodeId ?? failedNodeId,
    currentNodeId: ctx.currentNodeId ?? failedNodeId,
  });
}
