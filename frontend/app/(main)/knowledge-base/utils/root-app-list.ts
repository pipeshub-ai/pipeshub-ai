import { useKnowledgeBaseStore } from '../store';
import { KnowledgeHubApi } from '../api';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import { isKbCollectionsHubApp } from './all-records-transformer';
import { categorizeNodes, withOpenFoldersRestored } from './tree-builder';
import {
  sidebarNodeChildrenMetaAfterPage,
  type SidebarNodeChildrenPaginationMeta,
} from './sidebar-child-pagination-meta';
import type { KnowledgeHubApiResponse, KnowledgeHubNode, NodeType } from '../types';

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

type ChildrenQuery = NonNullable<Parameters<typeof KnowledgeHubApi.getNodeChildren>[2]>;

// Children lists reach the sidebar in two shapes: pages of 20 by name, which
// carry a cursor, and single lists loaded elsewhere (the page opening a path,
// the move dialog) in the hub's default order. A reload must ask for the same
// list again, or rows the user saw drop out.
const singleListQueryById = new Map<string, ChildrenQuery>();

/**
 * Writes a children list, its cursor (null for a single list) and the query
 * that loaded it in one step, so a cursor never describes a different list
 * than the cache holds when two loads of the same folder race.
 */
export function storeChildrenList(
  parentId: string,
  items: KnowledgeHubNode[],
  loaded: { query: ChildrenQuery; cursor: SidebarNodeChildrenPaginationMeta | null },
): void {
  const { cacheNodeChildren, setNodeChildrenPagination } = useKnowledgeBaseStore.getState();
  cacheNodeChildren(parentId, items);
  setNodeChildrenPagination(parentId, loaded.cursor);
  singleListQueryById.set(parentId, loaded.query);
}

async function reloadChildren(id: string, nodeType: NodeType): Promise<void> {
  const state = useKnowledgeBaseStore.getState();
  const cursor = state.nodeChildrenPagination.get(id);
  const cachedLength = state.nodeChildrenCache.get(id)?.length ?? 0;
  const byId = new Map<string, KnowledgeHubNode>();

  if (cursor) {
    const query: ChildrenQuery = {
      onlyContainers: true,
      limit: SIDEBAR_PAGINATION_PAGE_SIZE,
      include: 'counts',
      sortBy: 'name',
      sortOrder: 'asc',
    };
    const pagesLoaded = Math.max(1, cursor.hasNext ? cursor.nextPage - 1 : cursor.nextPage);
    let next = cursor;
    for (let page = 1; page <= pagesLoaded; page += 1) {
      const response = await KnowledgeHubApi.getNodeChildren(nodeType, id, { ...query, page });
      for (const item of response.items) byId.set(item.id, item);
      next = sidebarNodeChildrenMetaAfterPage(response.pagination, response.items.length, SIDEBAR_PAGINATION_PAGE_SIZE, page, nodeType);
      if (!next.hasNext) break;
    }
    // Another load replaced this list meanwhile; its rows and cursor stand.
    if (useKnowledgeBaseStore.getState().nodeChildrenPagination.get(id) !== cursor) return;
    storeChildrenList(id, [...byId.values()], { query: { ...query, page: 1 }, cursor: next });
  } else {
    const query = singleListQueryById.get(id) ?? { onlyContainers: true, page: 1, limit: 50 };
    const response = await KnowledgeHubApi.getNodeChildren(nodeType, id, {
      ...query,
      page: 1,
      limit: Math.max(query.limit ?? 50, cachedLength),
    });
    if (useKnowledgeBaseStore.getState().nodeChildrenPagination.get(id)) return;
    for (const item of response.items) byId.set(item.id, item);
    storeChildrenList(id, [...byId.values()], { query, cursor: null });
  }

  useKnowledgeBaseStore.getState().addNodes([...byId.values()]);
}

/**
 * Fetches fresh children for every open folder under `rootIds` (after a
 * rename, any of them may show an old name), each one the way it was first
 * loaded, so the same rows come back and "load more" carries on from there.
 */
export async function reloadOpenFoldersUnder(rootIds: string[]): Promise<void> {
  const { expandedFolders, nodeChildrenCache, nodes } = useKnowledgeBaseStore.getState();
  const open: string[] = [];
  const seen = new Set<string>();
  const queue = [...rootIds];
  while (queue.length > 0) {
    const id = queue.shift()!;
    if (seen.has(id)) continue;
    seen.add(id);
    if (!expandedFolders[id]) continue;
    open.push(id);
    for (const child of nodeChildrenCache.get(id) ?? []) queue.push(child.id);
  }

  for (const id of open) {
    await reloadChildren(id, (nodes.find((n) => n.id === id)?.nodeType ?? 'folder') as NodeType);
  }
  restoreOpenFoldersInSidebar();
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
