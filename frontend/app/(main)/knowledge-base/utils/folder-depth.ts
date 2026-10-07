import type { Breadcrumb } from '../types';

/**
 * How many folders deep the open node sits; 0 at a collection root. Only the
 * crumbs below the collection count, so a root the API labels `folder` is not
 * mistaken for a level.
 */
export function folderDepthOf(
  breadcrumbs: Breadcrumb[] | null | undefined,
  collectionId?: string | null,
): number {
  const crumbs = breadcrumbs ?? [];
  const rootIndex = collectionId ? crumbs.findIndex((crumb) => crumb.id === collectionId) : -1;
  return crumbs.slice(rootIndex + 1).filter((crumb) => crumb.nodeType === 'folder').length;
}

/** Folder levels a relative upload path adds: 'a/b/c.txt' -> 2. */
export function folderLevelsInPath(path: string): number {
  return Math.max(path.split('/').filter(Boolean).length - 1, 0);
}

/** The path a file inside an uploaded folder is sent with. */
export function uploadPathOf(folderName: string, relativePath: string | undefined, fileName: string): string {
  return `${folderName}/${relativePath || fileName}`;
}
