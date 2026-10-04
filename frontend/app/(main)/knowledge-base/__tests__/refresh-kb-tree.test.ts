import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useKnowledgeBaseStore } from '../store';
import { resetKnowledgeBaseSession } from '../utils/sidebar-session';
import { refreshKbTree } from '../utils/refresh-kb-tree';
import { loadMoreNodeChildrenPage, loadMoreRootAppList } from '../utils/sidebar-paginated-fetch';
import { loadRootAppListFirstPage } from '../utils/root-app-list';
import {
  loadNextChildrenPage,
  openFolderChildren,
  reloadOpenFoldersUnder,
  storeChildrenList,
} from '../utils/folder-children';
import { collection, hubNode, hubResponse } from './kb-page-harness';
import type { KnowledgeHubNode } from '../types';

const getNavigationNodes = vi.hoisted(() => vi.fn());
const getNodeChildren = vi.hoisted(() => vi.fn());
vi.mock('../api', () => ({
  KnowledgeHubApi: { getNavigationNodes, getNodeChildren },
  forgetPendingNodeChildrenRequests: () => {},
}));

const ENGINEERING = collection('kb-eng', 'Engineering');
const DRIVE = hubNode({ id: 'app-drive', name: 'Google Drive', nodeType: 'app', origin: 'CONNECTOR', connector: 'DRIVE' });

function sidebarCollectionIds() {
  const tree = useKnowledgeBaseStore.getState().categorizedNodes;
  return [...(tree?.shared ?? []), ...(tree?.private ?? [])].map((n) => n.id);
}

function sidebarCollectionNames() {
  const tree = useKnowledgeBaseStore.getState().categorizedNodes;
  return [...(tree?.shared ?? []), ...(tree?.private ?? [])].map((n) => n.name);
}

function cachedCollectionIds() {
  return useKnowledgeBaseStore
    .getState()
    .appNodes.filter((n) => n.connector === 'KB')
    .map((n) => n.id);
}

function connectors(count: number, offset = 0) {
  return Array.from({ length: count }, (_, i) =>
    hubNode({ id: `app-${offset + i}`, name: `Connector ${offset + i}`, nodeType: 'app', origin: 'CONNECTOR', connector: 'DRIVE' }),
  );
}

function pages(itemsByPage: KnowledgeHubNode[][]) {
  getNavigationNodes.mockImplementation(async ({ page }: { page: number }) => {
    const items = itemsByPage[page - 1] ?? [];
    const hasNext = page < itemsByPage.length;
    return hubResponse(items, {
      pagination: { page, limit: 20, totalItems: 0, totalPages: itemsByPage.length, hasNext, hasPrev: page > 1 },
    });
  });
}

beforeEach(() => {
  // Loads a previous test left in flight must not leak into this one.
  resetKnowledgeBaseSession();
  useKnowledgeBaseStore.setState(useKnowledgeBaseStore.getInitialState(), true);
  const { setAppNodes, setNodes, setCategorizedNodes } = useKnowledgeBaseStore.getState();
  setAppNodes([ENGINEERING]);
  setNodes([ENGINEERING]);
  setCategorizedNodes({ shared: [], private: [] });
  getNavigationNodes.mockReset();
  getNodeChildren.mockReset();
});

describe('refreshKbTree', () => {
  it('empties the sidebar when the server reports no collections at all', async () => {
    getNavigationNodes.mockResolvedValue(hubResponse([DRIVE]));

    await refreshKbTree();

    expect(sidebarCollectionIds()).toEqual([]);
  });

  it('finds the collections that sit behind a full page of connectors', async () => {
    pages([connectors(20), [collection('kb-eng', 'Engineering (renamed)')]]);

    await refreshKbTree();

    expect(sidebarCollectionIds()).toEqual(['kb-eng']);
    expect(sidebarCollectionNames()).toEqual(['Engineering (renamed)']);
    expect(getNavigationNodes).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }));
  });

  it('does not bring back a deleted collection when connectors fill the first page', async () => {
    const { setAppNodes, setNodes } = useKnowledgeBaseStore.getState();
    setAppNodes([collection('kb-gone', 'Deleted'), ENGINEERING]);
    setNodes([ENGINEERING]);
    pages([connectors(20), [ENGINEERING]]);

    await refreshKbTree();

    expect(sidebarCollectionIds()).toEqual(['kb-eng']);
  });

  it('empties the sidebar when no page of the list has a collection', async () => {
    pages([connectors(20), connectors(3, 20)]);

    await refreshKbTree();

    expect(sidebarCollectionIds()).toEqual([]);
    expect(getNavigationNodes).toHaveBeenCalledTimes(2);
  });

  it('keeps collections from every page, not just the first one that has any', async () => {
    pages([connectors(20), [collection('kb-a', 'Alpha')], [collection('kb-b', 'Beta')]]);

    await refreshKbTree();

    expect(sidebarCollectionIds().sort()).toEqual(['kb-a', 'kb-b']);
    expect(useKnowledgeBaseStore.getState().appRootListPagination).toEqual({ hasNext: false, nextPage: 3 });

    await loadMoreRootAppList();

    expect(getNavigationNodes).toHaveBeenCalledTimes(3);
    expect(cachedCollectionIds().sort()).toEqual(['kb-a', 'kb-b']);
  });

  it('carries on loading after the last page it read when the list is longer than it walks', async () => {
    const lastCollectionPage = [collection('kb-b', 'Beta')];
    pages([
      connectors(20),
      [collection('kb-a', 'Alpha')],
      ...Array.from({ length: 47 }, (_, i) => connectors(20, 100 + i * 20)),
      lastCollectionPage,
      ...Array.from({ length: 10 }, (_, i) => connectors(20, 2000 + i * 20)),
    ]);

    await refreshKbTree();

    expect(getNavigationNodes).toHaveBeenCalledTimes(50);
    expect(sidebarCollectionIds().sort()).toEqual(['kb-a', 'kb-b']);
    expect(useKnowledgeBaseStore.getState().appRootListPagination).toEqual({ hasNext: true, nextPage: 51 });

    await loadMoreRootAppList();

    expect(getNavigationNodes).toHaveBeenLastCalledWith(expect.objectContaining({ page: 51 }));
    expect(cachedCollectionIds().sort()).toEqual(['kb-a', 'kb-b']);
  });

  it('leaves the sidebar as it was when a later page fails to load', async () => {
    const before = useKnowledgeBaseStore.getState();
    getNavigationNodes.mockImplementation(async ({ page }: { page: number }) => {
      if (page === 2) throw new Error('offline');
      return hubResponse(connectors(20), {
        pagination: { page, limit: 20, totalItems: 0, totalPages: 3, hasNext: true, hasPrev: false },
      });
    });

    await expect(refreshKbTree()).rejects.toThrow('offline');

    const after = useKnowledgeBaseStore.getState();
    expect(after.appNodes).toBe(before.appNodes);
    expect(after.nodes).toBe(before.nodes);
    expect(after.appRootListPagination).toBe(before.appRootListPagination);
  });

  it('lets the newest refresh win when an older one finishes after it', async () => {
    let releaseOldPage2: (value: unknown) => void = () => {};
    let call = 0;
    getNavigationNodes.mockImplementation(({ page }: { page: number }) => {
      call += 1;
      if (call === 1) {
        return Promise.resolve(hubResponse([collection('kb-b', 'Beta')], {
          pagination: { page, limit: 20, totalItems: 21, totalPages: 2, hasNext: true, hasPrev: false },
        }));
      }
      if (call === 2) return new Promise((resolve) => { releaseOldPage2 = resolve; });
      return Promise.resolve(hubResponse([collection('kb-a', 'Alpha')]));
    });

    const older = refreshKbTree();
    await vi.waitFor(() => expect(getNavigationNodes).toHaveBeenCalledTimes(2));
    await refreshKbTree();
    expect(sidebarCollectionIds()).toEqual(['kb-a']);

    releaseOldPage2(hubResponse([], {
      pagination: { page: 2, limit: 20, totalItems: 21, totalPages: 2, hasNext: false, hasPrev: true },
    }));
    await older;

    expect(sidebarCollectionIds()).toEqual(['kb-a']);
    expect(cachedCollectionIds()).toEqual(['kb-a']);
  });

  it('ignores a first-page load that finishes after a newer refresh', async () => {
    let releaseFirstPage: (value: unknown) => void = () => {};
    getNavigationNodes.mockImplementationOnce(() => new Promise((resolve) => { releaseFirstPage = resolve; }));
    const firstLoad = loadRootAppListFirstPage();
    getNavigationNodes.mockResolvedValue(hubResponse([collection('kb-new', 'Handbook'), ENGINEERING]));
    await refreshKbTree();

    releaseFirstPage(hubResponse([ENGINEERING], {
      pagination: { page: 1, limit: 20, totalItems: 40, totalPages: 2, hasNext: true, hasPrev: false },
    }));

    await expect(firstLoad).resolves.toBe(false);
    expect(cachedCollectionIds().sort()).toEqual(['kb-eng', 'kb-new']);
    expect(useKnowledgeBaseStore.getState().appRootListPagination).toEqual({ hasNext: false, nextPage: 1 });
  });

  it('ignores a "load more" page that arrives after a refresh started', async () => {
    useKnowledgeBaseStore.getState().setAppRootListPagination({ hasNext: true, nextPage: 2 });
    let releaseLoadMore: (value: unknown) => void = () => {};
    getNavigationNodes.mockImplementationOnce(() => new Promise((resolve) => { releaseLoadMore = resolve; }));
    const loadMore = loadMoreRootAppList();
    getNavigationNodes.mockResolvedValue(hubResponse([ENGINEERING]));
    await refreshKbTree();

    releaseLoadMore(hubResponse([collection('kb-gone', 'Deleted meanwhile')], {
      pagination: { page: 2, limit: 20, totalItems: 40, totalPages: 3, hasNext: true, hasPrev: true },
    }));
    await loadMore;

    expect(cachedCollectionIds()).toEqual(['kb-eng']);
    expect(sidebarCollectionIds()).toEqual(['kb-eng']);
    expect(useKnowledgeBaseStore.getState().appRootListPagination).toEqual({ hasNext: false, nextPage: 1 });
  });

  it.each([
    { label: 'a walk that reads the whole list', totalPages: 3, walked: 3, cursor: { hasNext: false, nextPage: 3 } },
    { label: 'a walk that stops at its page limit', totalPages: 60, walked: 50, cursor: { hasNext: true, nextPage: 51 } },
  ])('keeps the paging from $label when "load more" is clicked during it', async ({ totalPages, walked, cursor }) => {
    useKnowledgeBaseStore.getState().setAppRootListPagination({ hasNext: true, nextPage: 2 });
    const pageItems = (page: number) =>
      page === 1 ? [collection('kb-a', 'Alpha')] : page === walked ? [collection('kb-b', 'Beta')] : connectors(20, page * 20);
    const respond = (page: number) =>
      hubResponse(pageItems(page), {
        pagination: { page, limit: 20, totalItems: 0, totalPages, hasNext: page < totalPages, hasPrev: page > 1 },
      });
    let releaseWalk: () => void = () => {};
    getNavigationNodes.mockImplementation(({ page }: { page: number }) =>
      page === 1
        ? new Promise((resolve) => { releaseWalk = () => resolve(respond(1)); })
        : Promise.resolve(respond(page)),
    );

    const walk = refreshKbTree();
    await loadMoreRootAppList();
    expect(getNavigationNodes).toHaveBeenCalledTimes(1);
    releaseWalk();
    await walk;

    expect(useKnowledgeBaseStore.getState().appRootListPagination).toEqual(cursor);
    expect(sidebarCollectionIds().sort()).toEqual(['kb-a', 'kb-b']);
    expect(cachedCollectionIds().sort()).toEqual(['kb-a', 'kb-b']);
  });

  it('drops a "load more" page that arrives while a refresh is still reading pages', async () => {
    useKnowledgeBaseStore.getState().setAppRootListPagination({ hasNext: true, nextPage: 51 });
    const respond = (page: number, items: KnowledgeHubNode[], hasNext: boolean) =>
      hubResponse(items, { pagination: { page, limit: 20, totalItems: 0, totalPages: 60, hasNext, hasPrev: page > 1 } });
    let releaseWalk: () => void = () => {};
    let releaseLoadMore: () => void = () => {};
    getNavigationNodes.mockImplementation(({ page }: { page: number }) => {
      if (page === 1) return new Promise((resolve) => { releaseWalk = () => resolve(respond(1, [ENGINEERING], false)); });
      return new Promise((resolve) => { releaseLoadMore = () => resolve(respond(51, [collection('kb-late', 'Page 51')], true)); });
    });

    const walk = refreshKbTree();
    const loadMore = loadMoreRootAppList();
    releaseLoadMore();
    await loadMore;

    expect(sidebarCollectionIds()).not.toContain('kb-late');
    expect(cachedCollectionIds()).not.toContain('kb-late');

    releaseWalk();
    await walk;

    expect(sidebarCollectionIds()).toEqual(['kb-eng']);
    expect(cachedCollectionIds()).toEqual(['kb-eng']);
    expect(useKnowledgeBaseStore.getState().appRootListPagination).toEqual({ hasNext: false, nextPage: 1 });
  });

  it('does not start a "load more" while a refresh is reading pages', async () => {
    useKnowledgeBaseStore.getState().setAppRootListPagination({ hasNext: true, nextPage: 2 });
    let releaseWalk: () => void = () => {};
    getNavigationNodes.mockImplementation(({ page }: { page: number }) =>
      page === 1
        ? new Promise((resolve) => { releaseWalk = () => resolve(hubResponse([ENGINEERING])); })
        : Promise.resolve(hubResponse([collection('kb-late', 'Page 2')])),
    );

    const walk = refreshKbTree();
    await loadMoreRootAppList();

    expect(getNavigationNodes).toHaveBeenCalledTimes(1);
    releaseWalk();
    await walk;
  });

  it('adds a collection found by "load more" to the sidebar', async () => {
    pages([
      [collection('kb-a', 'Alpha')],
      ...Array.from({ length: 49 }, (_, i) => connectors(20, 100 + i * 20)),
      [collection('kb-late', 'Found on page 51')],
    ]);
    await refreshKbTree();
    expect(sidebarCollectionIds()).toEqual(['kb-a']);

    await loadMoreRootAppList();

    expect(getNavigationNodes).toHaveBeenLastCalledWith(expect.objectContaining({ page: 51 }));
    expect(sidebarCollectionIds().sort()).toEqual(['kb-a', 'kb-late']);
    expect(useKnowledgeBaseStore.getState().nodes.map((n) => n.id).sort()).toEqual(['kb-a', 'kb-late']);
  });

  it('keeps folders open at every depth, whichever section their collection is in', async () => {
    const shared = collection('kb-eng', 'Engineering', { sharingStatus: 'shared' });
    const designs = hubNode({ id: 'folder-designs', name: 'Designs', nodeType: 'folder', parentId: 'kb-eng', hasChildren: true });
    const mockups = hubNode({ id: 'folder-mockups', name: 'Mockups', nodeType: 'folder', parentId: 'folder-designs' });
    const kb = useKnowledgeBaseStore.getState();
    kb.setNodes([shared, designs, mockups]);
    kb.cacheNodeChildren('kb-eng', [designs]);
    kb.cacheNodeChildren('folder-designs', [mockups]);
    kb.toggleFolderExpanded('kb-eng');
    kb.toggleFolderExpanded('folder-designs');
    getNavigationNodes.mockResolvedValue(hubResponse([shared]));

    await refreshKbTree();

    const tree = useKnowledgeBaseStore.getState().categorizedNodes;
    const eng = tree?.shared.find((n) => n.id === 'kb-eng');
    expect(eng?.children.map((c) => c.id)).toEqual(['folder-designs']);
    expect(eng?.children[0].children.map((c) => c.id)).toEqual(['folder-mockups']);
    expect(useKnowledgeBaseStore.getState().nodes.map((n) => n.id).sort()).toEqual(['folder-designs', 'folder-mockups', 'kb-eng']);
  });

  it('drops a "load more" page for a folder whose list was replaced while it loaded', async () => {
    const byName = Array.from({ length: 20 }, (_, i) =>
      hubNode({ id: `a-${i}`, name: `A ${String(i).padStart(2, '0')}`, nodeType: 'folder', parentId: 'folder-designs' }),
    );
    const newest = Array.from({ length: 50 }, (_, i) =>
      hubNode({ id: `n-${i}`, name: `N ${String(i).padStart(2, '0')}`, nodeType: 'folder', parentId: 'folder-designs' }),
    );
    const kb = useKnowledgeBaseStore.getState();
    kb.cacheNodeChildren('folder-designs', byName);
    kb.setNodeChildrenPagination('folder-designs', { hasNext: true, nextPage: 2, nodeType: 'folder' });
    let release: () => void = () => {};
    getNodeChildren.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () =>
            resolve(hubResponse([hubNode({ id: 'b-0', name: 'B 00', nodeType: 'folder', parentId: 'folder-designs' })], {
              pagination: { page: 2, limit: 20, totalItems: 41, totalPages: 3, hasNext: true, hasPrev: true },
            }));
        }),
    );

    const loadMore = loadMoreNodeChildrenPage('folder-designs');
    const reloaded = { hasNext: true, nextPage: 4, nodeType: 'folder' as const };
    storeChildrenList('folder-designs', newest, reloaded);
    release();
    await loadMore;

    const state = useKnowledgeBaseStore.getState();
    expect(state.nodeChildrenCache.get('folder-designs')?.map((n) => n.id)).toEqual(newest.map((n) => n.id));
    expect(state.nodeChildrenPagination.get('folder-designs')).toBe(reloaded);
  });

  it('lets two overlapping walks of one folder each find their own row', async () => {
    const folders = Array.from({ length: 50 }, (_, i) =>
      hubNode({ id: `f-${i}`, name: `F ${String(i).padStart(2, '0')}`, nodeType: 'folder', parentId: 'folder-designs' }),
    );
    const heldPageTwo: Array<() => void> = [];
    getNodeChildren.mockImplementation(async (_type: string, _id: string, params: { page?: number }) => {
      const page = params.page ?? 1;
      if (page === 2) await new Promise<void>((resolve) => heldPageTwo.push(resolve));
      return hubResponse(folders.slice((page - 1) * 20, page * 20), {
        pagination: { page, limit: 20, totalItems: 50, totalPages: 3, hasNext: page < 3, hasPrev: page > 1 },
      });
    });

    const wantsPageTwo = openFolderChildren('folder-designs', 'folder', { until: (c) => c.some((n) => n.id === 'f-25') });
    const wantsPageThree = openFolderChildren('folder-designs', 'folder', { until: (c) => c.some((n) => n.id === 'f-45') });
    await vi.waitFor(() => expect(heldPageTwo.length).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (const release of heldPageTwo) release();
    await Promise.all([wantsPageTwo, wantsPageThree]);

    const cached = useKnowledgeBaseStore.getState().nodeChildrenCache.get('folder-designs')?.map((n) => n.id) ?? [];
    expect(cached).toContain('f-25');
    expect(cached).toContain('f-45');
  });

  it('does not rebuild a folder list from a late page after the list was purged', async () => {
    const onlyChild = hubNode({ id: 'folder-x', name: 'X', nodeType: 'folder', parentId: 'folder-designs' });
    const kb = useKnowledgeBaseStore.getState();
    kb.cacheNodeChildren('folder-designs', [onlyChild]);
    kb.setNodeChildrenPagination('folder-designs', { hasNext: true, nextPage: 2, nodeType: 'folder' });
    let release: () => void = () => {};
    getNodeChildren.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () =>
            resolve(hubResponse([hubNode({ id: 'folder-late', name: 'Late', nodeType: 'folder', parentId: 'folder-designs' })], {
              pagination: { page: 2, limit: 20, totalItems: 21, totalPages: 2, hasNext: false, hasPrev: true },
            }));
        }),
    );

    const loading = loadNextChildrenPage('folder-designs');
    useKnowledgeBaseStore.getState().purgeDeletedIdsFromSidebarChildrenCaches(['folder-x']);
    release();
    await loading;

    expect(useKnowledgeBaseStore.getState().nodeChildrenCache.has('folder-designs')).toBe(false);
  });

  it('reloads a list with the type its cursor was read with', async () => {
    const kb = useKnowledgeBaseStore.getState();
    kb.setNodes([]);
    kb.cacheNodeChildren('kb-eng', [hubNode({ id: 'folder-a', name: 'A', nodeType: 'folder', parentId: 'kb-eng' })]);
    kb.setNodeChildrenPagination('kb-eng', { hasNext: false, nextPage: 1, nodeType: 'app' });
    kb.toggleFolderExpanded('kb-eng');
    getNodeChildren.mockResolvedValue(hubResponse([hubNode({ id: 'folder-a', name: 'A renamed', nodeType: 'folder', parentId: 'kb-eng' })]));

    await reloadOpenFoldersUnder(['kb-eng']);

    expect(getNodeChildren).toHaveBeenCalledWith('app', 'kb-eng', expect.anything());
    expect(useKnowledgeBaseStore.getState().nodeChildrenPagination.get('kb-eng')?.nodeType).toBe('app');
    expect(useKnowledgeBaseStore.getState().nodeChildrenCache.get('kb-eng')?.[0].name).toBe('A renamed');
  });

  it('shows the collections the server returned', async () => {
    getNavigationNodes.mockResolvedValue(hubResponse([collection('kb-new', 'Handbook'), DRIVE]));
    const after = vi.fn();

    await refreshKbTree(after);

    expect(sidebarCollectionIds()).toEqual(['kb-new']);
    expect(after).toHaveBeenCalledTimes(1);
  });
});
