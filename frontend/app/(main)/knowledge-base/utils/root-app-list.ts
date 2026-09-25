import { useKnowledgeBaseStore } from '../store';
import { KnowledgeHubApi } from '../api';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import { isKbCollectionsHubApp } from './all-records-transformer';
import { categorizeNodes, withOpenFoldersRestored } from './tree-builder';
import type { KnowledgeHubApiResponse, KnowledgeHubNode } from '../types';

// Several loads write the root app list: the first-page load when the page
// opens, the full refresh after a create/rename/delete, and "load more". They
// can overlap, and whichever finished last used to win, so a slower, older
// response could hide a collection just created or restore one just deleted.
let rootListGeneration = 0;
// A replacing load writes only when it finishes, so until then the cursor
// still looks current to "load more". Only the newest load counts: an older
// one that never settles must not block "load more" for good.
let newestReplacingLoadDone = true;

/**
 * Runs a load that replaces the list. `isCurrent` turns false once a newer
 * one starts, so a load that finishes late can skip its writes.
 */
export async function runReplacingRootListLoad<T>(
  load: (isCurrent: () => boolean) => Promise<T>,
): Promise<T> {
  const generation = ++rootListGeneration;
  const isCurrent = () => generation === rootListGeneration;
  newestReplacingLoadDone = false;
  try {
    return await load(isCurrent);
  } finally {
    if (isCurrent()) newestReplacingLoadDone = true;
  }
}

/** True while the newest load that will replace the whole list is still reading. */
export function isReplacingRootListLoadInFlight(): boolean {
  return !newestReplacingLoadDone;
}

/** For a load that extends the list. The check turns false once any replacing load starts. */
export function watchRootList(): () => boolean {
  const generation = rootListGeneration;
  return () => generation === rootListGeneration;
}

/** Makes every root-list load still in flight stale and clears the running flag (sign-out). */
export function resetRootListLoads(): void {
  rootListGeneration += 1;
  newestReplacingLoadDone = true;
}

export function fetchRootAppPage(page: number): Promise<KnowledgeHubApiResponse> {
  return KnowledgeHubApi.getNavigationNodes({
    page,
    limit: SIDEBAR_PAGINATION_PAGE_SIZE,
    include: 'counts',
    sortBy: 'updatedAt',
    sortOrder: 'desc',
  });
}

export function rootListPaginationAfter(
  pagination: KnowledgeHubApiResponse['pagination'] | undefined,
): { hasNext: boolean; nextPage: number } | null {
  return pagination
    ? { hasNext: pagination.hasNext, nextPage: pagination.hasNext ? pagination.page + 1 : pagination.page }
    : null;
}

export function collectionsFirst(appItems: KnowledgeHubNode[]): KnowledgeHubNode[] {
  return [
    ...appItems.filter((n) => isKbCollectionsHubApp(n)),
    ...appItems.filter((n) => !isKbCollectionsHubApp(n)),
  ];
}

/**
 * Loads the first page of root apps. Resolves `false`, without writing, when a
 * newer load started meanwhile; a failure is thrown only while still current,
 * so the caller's error handling never clobbers newer data.
 */
export async function loadRootAppListFirstPage(): Promise<boolean> {
  return runReplacingRootListLoad(async (isCurrent) => {
    let response: KnowledgeHubApiResponse;
    try {
      response = await fetchRootAppPage(1);
    } catch (error) {
      if (!isCurrent()) return false;
      throw error;
    }
    if (!isCurrent()) return false;

    const { setAppNodes, setAppRootListPagination } = useKnowledgeBaseStore.getState();
    setAppNodes(collectionsFirst(response.items.filter((n) => n.nodeType === 'app')));
    setAppRootListPagination(rootListPaginationAfter(response.pagination));
    return true;
  });
}

/** Puts the cached children of every open folder back into the sidebar tree, at any depth. */
export function restoreOpenFoldersInSidebar(): void {
  const { categorizedNodes, nodeChildrenCache, expandedFolders, setCategorizedNodes } =
    useKnowledgeBaseStore.getState();
  if (!categorizedNodes) return;
  setCategorizedNodes({
    shared: withOpenFoldersRestored(categorizedNodes.shared, nodeChildrenCache, expandedFolders),
    private: withOpenFoldersRestored(categorizedNodes.private, nodeChildrenCache, expandedFolders),
  });
}

/**
 * Replaces the collections shown in the sidebar and keeps open folders open.
 * Folders loaded by expanding stay in `nodes` while their collection is still
 * listed: the sidebar's expand handler looks them up there.
 */
export function showCollectionsInSidebar(collections: KnowledgeHubNode[]): void {
  const { nodes, nodeChildrenCache, setNodes, setCategorizedNodes } = useKnowledgeBaseStore.getState();
  const reachable = new Set(collections.map((n) => n.id));
  const queue = [...reachable];
  while (queue.length > 0) {
    for (const child of nodeChildrenCache.get(queue.pop()!) ?? []) {
      if (!reachable.has(child.id)) {
        reachable.add(child.id);
        queue.push(child.id);
      }
    }
  }
  setNodes([...collections, ...nodes.filter((n) => n.nodeType !== 'app' && reachable.has(n.id))]);
  setCategorizedNodes(categorizeNodes(collections, null));
  restoreOpenFoldersInSidebar();
}
