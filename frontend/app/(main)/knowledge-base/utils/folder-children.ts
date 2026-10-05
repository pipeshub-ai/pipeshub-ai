import { useKnowledgeBaseStore } from '../store';
import { KnowledgeHubApi } from '../api';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import { effectiveHasChildrenAfterSidebarExpand, mergeChildrenIntoSections, treeHasNodeWithId } from './tree-builder';
import {
  sidebarNodeChildrenMetaAfterPage,
  type SidebarNodeChildrenPaginationMeta,
} from './sidebar-child-pagination-meta';
import { restoreOpenFoldersInSidebar } from './root-app-list';
import { kbSessionToken } from './kb-session';
import type { KnowledgeHubNode, NodeType } from '../types';

/**
 * The one way the sidebar tree lists a folder's (or collection's) children:
 * name order, pages of SIDEBAR_PAGINATION_PAGE_SIZE, with a cursor for the
 * next page. Everything that shows children in the Collections tree or the
 * move dialog goes through here, so a cached list and its cursor always
 * describe the same pages of the same order.
 */
const CHILDREN_QUERY = {
  onlyContainers: true,
  limit: SIDEBAR_PAGINATION_PAGE_SIZE,
  include: 'counts',
  sortBy: 'name',
  sortOrder: 'asc',
} as const;

/** Bounds a walk that reads further pages to find a row (defensive). */
const MAX_PAGES_PER_WALK = 10;

// One load per folder and page at a time: the page's path expansion, the
// sidebar's auto-expand, a chevron click and the move dialog often ask for the
// same folder at once, and two writers racing is how a list and its cursor
// used to drift apart.
const firstPageLoads = new Map<string, Promise<void>>();
const nextPageLoads = new Map<string, Promise<boolean>>();

function nodeTypeOf(id: string, fallback: NodeType): NodeType {
  return (useKnowledgeBaseStore.getState().nodes.find((n) => n.id === id)?.nodeType ?? fallback) as NodeType;
}

function fetchChildrenPage(id: string, nodeType: NodeType, page: number) {
  return KnowledgeHubApi.getNodeChildren(nodeType, id, { ...CHILDREN_QUERY, page });
}

/** Writes a children list and its cursor in one step. */
export function storeChildrenList(
  parentId: string,
  items: KnowledgeHubNode[],
  cursor: SidebarNodeChildrenPaginationMeta,
): void {
  const { cacheNodeChildren, setNodeChildrenPagination } = useKnowledgeBaseStore.getState();
  cacheNodeChildren(parentId, items);
  setNodeChildrenPagination(parentId, cursor);
}

/** Shows a folder's cached children in the Collections tree and in any connector tree that holds it. */
export function showFolderChildren(parentId: string): void {
  const state = useKnowledgeBaseStore.getState();
  const children = state.nodeChildrenCache.get(parentId);
  if (!children) return;
  const hasChildFolders = effectiveHasChildrenAfterSidebarExpand(children);
  if (children.length > 0) state.addNodes(children);
  const latest = useKnowledgeBaseStore.getState();
  if (latest.categorizedNodes) {
    latest.setCategorizedNodes(mergeChildrenIntoSections(latest.categorizedNodes, parentId, children, hasChildFolders));
  }
  for (const [appId, tree] of Array.from(latest.connectorAppTrees.entries())) {
    if (!treeHasNodeWithId(tree, parentId)) continue;
    latest.mergeConnectorAppTreeChildren(appId, parentId, children, hasChildFolders);
    break;
  }
  // The merge rebuilds this folder's rows without their own children; put
  // back any open subfolder beneath it.
  restoreOpenFoldersInSidebar();
}

async function loadFirstPage(id: string, nodeType: NodeType): Promise<void> {
  const stillSignedIn = kbSessionToken();
  const response = await fetchChildrenPage(id, nodeType, 1);
  if (!stillSignedIn()) return;
  // A reload or another loader may have filled it while this was in flight.
  if (useKnowledgeBaseStore.getState().nodeChildrenCache.has(id)) return;
  storeChildrenList(
    id,
    response.items,
    sidebarNodeChildrenMetaAfterPage(response.pagination, response.items.length, SIDEBAR_PAGINATION_PAGE_SIZE, 1, nodeType),
  );
}

/**
 * Reads the next page of a folder's children and appends it. Callers asking
 * for the same folder while a page is loading share that load. Drops the page
 * if the folder's list was replaced meanwhile (a reload, or sign-out).
 * Returns whether the list may have grown; re-read the cursor either way.
 */
export function loadNextChildrenPage(parentId: string): Promise<boolean> {
  const running = nextPageLoads.get(parentId);
  if (running) return running;
  const load = readNextPage(parentId).finally(() => nextPageLoads.delete(parentId));
  nextPageLoads.set(parentId, load);
  return load;
}

async function readNextPage(parentId: string): Promise<boolean> {
  const state = useKnowledgeBaseStore.getState();
  const cursor = state.nodeChildrenPagination.get(parentId);
  if (!cursor?.hasNext) return false;
  const stillSignedIn = kbSessionToken();
  const response = await fetchChildrenPage(parentId, cursor.nodeType, cursor.nextPage);
  const latest = useKnowledgeBaseStore.getState();
  if (!stillSignedIn() || latest.nodeChildrenPagination.get(parentId) !== cursor) return false;
  // Purging a deleted child can drop the list while leaving its cursor; this
  // page alone would then pass for the whole list.
  const shown = latest.nodeChildrenCache.get(parentId);
  if (!shown) return false;

  const byId = new Map(shown.map((n) => [n.id, n]));
  for (const item of response.items) byId.set(item.id, item);
  storeChildrenList(
    parentId,
    [...byId.values()],
    sidebarNodeChildrenMetaAfterPage(
      response.pagination,
      response.items.length,
      SIDEBAR_PAGINATION_PAGE_SIZE,
      cursor.nextPage,
      cursor.nodeType,
    ),
  );
  return true;
}

/**
 * Makes sure a folder's children are cached, then shows them. With `until`,
 * keeps reading pages until it holds (e.g. the next folder on the page's path
 * is listed) or the list ends.
 */
export async function openFolderChildren(
  id: string,
  nodeType: NodeType,
  options: { until?: (children: KnowledgeHubNode[]) => boolean } = {},
): Promise<void> {
  if (!useKnowledgeBaseStore.getState().nodeChildrenCache.has(id)) {
    let load = firstPageLoads.get(id);
    if (!load) {
      load = loadFirstPage(id, nodeTypeOf(id, nodeType)).finally(() => firstPageLoads.delete(id));
      firstPageLoads.set(id, load);
    }
    await load;
  }
  // Decide from the cache and cursor each time, not from whether this caller's
  // own request won: an overlapping walk may have applied the page already.
  for (let step = 0; options.until && step < MAX_PAGES_PER_WALK; step += 1) {
    const state = useKnowledgeBaseStore.getState();
    if (options.until(state.nodeChildrenCache.get(id) ?? [])) break;
    if (!state.nodeChildrenPagination.get(id)?.hasNext) break;
    await loadNextChildrenPage(id);
  }
  showFolderChildren(id);
}

async function reloadChildren(id: string, fallbackNodeType: NodeType, keepVisibleId?: string): Promise<void> {
  const state = useKnowledgeBaseStore.getState();
  const cursor = state.nodeChildrenPagination.get(id);
  // The cursor records the type the list was read with (a collection is 'app').
  const nodeType = cursor?.nodeType ?? fallbackNodeType;
  const pagesLoaded = cursor ? Math.max(1, cursor.hasNext ? cursor.nextPage - 1 : cursor.nextPage) : 1;
  // A renamed row can sort past the pages that were shown; keep reading until
  // it is back in view rather than have it vanish right after the rename.
  const mustShow = keepVisibleId && (state.nodeChildrenCache.get(id) ?? []).some((n) => n.id === keepVisibleId)
    ? keepVisibleId
    : undefined;
  const stillSignedIn = kbSessionToken();
  const byId = new Map<string, KnowledgeHubNode>();
  let next: SidebarNodeChildrenPaginationMeta | undefined;
  for (let page = 1; page <= pagesLoaded + (mustShow ? MAX_PAGES_PER_WALK : 0); page += 1) {
    const response = await fetchChildrenPage(id, nodeType, page);
    for (const item of response.items) byId.set(item.id, item);
    next = sidebarNodeChildrenMetaAfterPage(response.pagination, response.items.length, SIDEBAR_PAGINATION_PAGE_SIZE, page, nodeType);
    if (!next.hasNext) break;
    if (page >= pagesLoaded && (!mustShow || byId.has(mustShow))) break;
  }
  if (!stillSignedIn() || !next) return;
  // Another load replaced this list meanwhile; its rows and cursor stand.
  if (useKnowledgeBaseStore.getState().nodeChildrenPagination.get(id) !== cursor) return;
  storeChildrenList(id, [...byId.values()], next);
  useKnowledgeBaseStore.getState().addNodes([...byId.values()]);
}

/**
 * Fetches fresh children for every open folder under `rootIds` (after a
 * rename, any of them may show an old name), reading as many pages as were
 * shown, so the same rows come back and "load more" carries on from there.
 * `keepVisibleId` (the renamed row) is read for until it is back in view.
 */
export async function reloadOpenFoldersUnder(rootIds: string[], keepVisibleId?: string): Promise<void> {
  const { expandedFolders, nodeChildrenCache } = useKnowledgeBaseStore.getState();
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
    await reloadChildren(id, nodeTypeOf(id, 'folder'), keepVisibleId);
  }
  restoreOpenFoldersInSidebar();
}

/** Forgets in-flight loads so a new session never waits on the previous one's. */
export function resetFolderChildrenLoads(): void {
  firstPageLoads.clear();
  nextPageLoads.clear();
}
