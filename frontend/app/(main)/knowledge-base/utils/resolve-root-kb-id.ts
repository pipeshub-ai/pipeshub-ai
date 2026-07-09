import type { Breadcrumb, CategorizedNodes } from '../types';
import { findNodeInCategorized } from './find-node';

const APPS_PREFIX = 'apps/';

/** Strip graph-style `apps/{id}` parent ids down to the raw KB/app key. */
export function normalizeKbId(id: string | null | undefined): string | undefined {
  if (!id) return undefined;
  const trimmed = id.trim();
  if (!trimmed) return undefined;
  return trimmed.startsWith(APPS_PREFIX) ? trimmed.slice(APPS_PREFIX.length) : trimmed;
}

/**
 * Resolve the root KB/collection id for folder-scoped CRUD (delete, rename, move).
 * Knowledge-hub items may expose parentId as `apps/{kbId}`; delete APIs expect the bare uuid.
 */
export function resolveRootKbIdFromContext(args: {
  breadcrumbs?: Breadcrumb[] | null;
  categorizedNodes?: CategorizedNodes | null;
  parentId?: string | null;
  fallbackKbId?: string | null;
  nodeId?: string;
}): string | undefined {
  const { breadcrumbs, categorizedNodes, parentId, fallbackKbId, nodeId } = args;

  if (breadcrumbs?.length && categorizedNodes) {
    const allRootNodes = [
      ...(categorizedNodes.shared ?? []),
      ...(categorizedNodes.private ?? []),
    ];
    const kbBreadcrumb = breadcrumbs.find(
      (b) =>
        allRootNodes.some((n) => n.id === b.id) ||
        b.nodeType === 'kb' ||
        b.nodeType === 'app'
    );
    if (kbBreadcrumb?.id) return normalizeKbId(kbBreadcrumb.id);
  }

  if (breadcrumbs?.length) {
    const appCrumb = breadcrumbs.find((b) => b.nodeType === 'app' || b.nodeType === 'kb');
    if (appCrumb?.id) return normalizeKbId(appCrumb.id);
  }

  if (nodeId && categorizedNodes) {
    const { rootKbId } = findNodeInCategorized(categorizedNodes, nodeId);
    if (rootKbId) return normalizeKbId(rootKbId);
  }

  const fromParent = normalizeKbId(parentId);
  if (fromParent) return fromParent;

  return normalizeKbId(fallbackKbId);
}
