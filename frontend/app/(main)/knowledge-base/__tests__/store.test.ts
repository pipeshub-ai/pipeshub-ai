/**
 * The Collections store: deleting and re-indexing (one item or a selection),
 * what the person is told, and how the sidebar and tables stay consistent
 * afterwards. The backend is faked at the axios adapter.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';
import type { KnowledgeHubNode } from '../types';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { useToastStore } = await import('@/lib/store/toast-store');
const { useKnowledgeBaseStore } = await import('../store');

const KB = '/api/v1/knowledgeBase';
const store = () => useKnowledgeBaseStore.getState();
const toasts = () => useToastStore.getState().toasts.map((t) => ({ variant: t.variant, title: t.title, description: t.description }));

function node(id: string, overrides: Partial<KnowledgeHubNode> = {}): KnowledgeHubNode {
  return { id, name: id, nodeType: 'record', ...overrides } as KnowledgeHubNode;
}

function tableWith(...ids: string[]) {
  return { items: ids.map((id) => node(id)) } as never;
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  store().reset();
  useToastStore.setState({ toasts: [] });
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('deleting one item', () => {
  it('removes a file from every table, selection and sidebar cache, then says so', async () => {
    const api = fakeApi({ [`DELETE ${KB}/record/r1`]: { status: 200 } });
    store().setTableData(tableWith('r1', 'r2'));
    store().setAllRecordsTableData(tableWith('r1'));
    store().setNodes([node('r1'), node('r2')]);
    store().selectItem('r1');
    store().selectRecord('r1');
    store().cacheNodeChildren('folder-1', [node('r1'), node('r2')]);
    const refresh = vi.fn(async () => {});

    await store().deleteNode('r1', 'record', 'kb-1', refresh);

    expect(api.sent.map((r) => `${r.method} ${r.url}`)).toEqual([`DELETE ${KB}/record/r1`]);
    expect(store().tableData?.items.map((i) => i.id)).toEqual(['r2']);
    expect(store().allRecordsTableData?.items).toEqual([]);
    expect(store().nodes.map((n) => n.id)).toEqual(['r2']);
    expect(store().selectedItems.has('r1')).toBe(false);
    expect(store().selectedRecords.has('r1')).toBe(false);
    expect(store().deletingNodeIds.size).toBe(0);
    expect(store().nodeChildrenCache.get('folder-1')?.map((n) => n.id)).toEqual(['r2']);
    expect(refresh).toHaveBeenCalledWith(['r1']);
    expect(toasts()).toEqual([{ variant: 'success', title: 'Deleted successfully', description: 'The file has been deleted.' }]);
  });

  it.each([
    ['a folder inside a collection', 'f1', 'folder', 'kb-1', `DELETE ${KB}/kb-1/folder/f1`, 'collection'],
    ['a whole collection', 'kb-1', 'kb', 'kb-1', `DELETE ${KB}/kb-1`, 'knowledge base'],
  ])('calls the right endpoint for %s', async (_label, id, type, kbId, route, noun) => {
    const api = fakeApi({ [route]: { status: 200 } });
    await store().deleteNode(id, type as never, kbId);
    expect(api.sent.map((r) => `${r.method} ${r.url}`)).toEqual([route]);
    expect(toasts()[0].description).toBe(`The ${noun} has been deleted.`);
  });

  it("keeps the item and shows the server's reason when the delete is refused", async () => {
    fakeApi({ [`DELETE ${KB}/record/r1`]: { status: 403, data: { message: 'Only the owner can delete this file.' } } });
    store().setTableData(tableWith('r1'));

    await expect(store().deleteNode('r1', 'record', 'kb-1')).rejects.toBeDefined();

    expect(store().tableData?.items.map((i) => i.id)).toEqual(['r1']);
    expect(store().deletingNodeIds.size).toBe(0);
    expect(toasts()).toEqual([{ variant: 'error', title: 'Delete failed', description: 'Only the owner can delete this file.' }]);
  });

  it('still reports success when only the follow-up refresh fails', async () => {
    fakeApi({ [`DELETE ${KB}/record/r1`]: { status: 200 } });
    await store().deleteNode('r1', 'record', 'kb-1', async () => {
      throw new Error('refresh failed');
    });
    expect(toasts().map((t) => t.variant)).toEqual(['success']);
  });
});

describe('deleting a selection', () => {
  const items = [
    { id: 'r1', name: 'a.pdf', nodeType: 'record' as const, kbId: 'kb-1' },
    { id: 'r2', name: 'b.pdf', nodeType: 'record' as const, kbId: 'kb-1' },
    { id: 'r3', name: 'c.pdf', nodeType: 'record' as const, kbId: 'kb-1' },
  ];

  it('counts successes and failures and refreshes with only the ids that went', async () => {
    fakeApi({
      [`DELETE ${KB}/record/r1`]: { status: 200 },
      [`DELETE ${KB}/record/r2`]: { status: 500 },
      [`DELETE ${KB}/record/r3`]: { status: 200 },
    });
    items.forEach((i) => store().selectItem(i.id));
    store().cacheNodeChildren('kb-1', [node('r1'), node('r2'), node('r3')]);
    const refresh = vi.fn(async () => {});

    await store().bulkDeleteSelected(items, refresh);

    expect(refresh).toHaveBeenCalledWith(['r1', 'r3']);
    expect(toasts()).toEqual([expect.objectContaining({ variant: 'warning', title: 'Deleted 2 items, 1 failed' })]);
    expect(store().selectedItems.size).toBe(0);
    expect(store().deletingNodeIds.size).toBe(0);
    expect(store().nodeChildrenCache.get('kb-1')?.map((n) => n.id)).toEqual(['r2']);
  });

  it('reports full success', async () => {
    fakeApi({ [`DELETE ${KB}/record/r1`]: { status: 200 }, [`DELETE ${KB}/record/r2`]: { status: 200 } });
    await store().bulkDeleteSelected(items.slice(0, 2));
    expect(toasts()).toEqual([expect.objectContaining({ variant: 'success', title: 'Successfully deleted 2 items' })]);
  });

  it('does not say the delete failed when only the refresh afterwards fails, and drops the deleted rows itself', async () => {
    fakeApi({ [`DELETE ${KB}/record/r1`]: { status: 200 }, [`DELETE ${KB}/record/r2`]: { status: 500 } });
    store().setTableData(tableWith('r1', 'r2', 'r3'));
    store().setAllRecordsTableData(tableWith('r1', 'r2'));
    store().setNodes([node('r1'), node('r2')]);

    await store().bulkDeleteSelected(items.slice(0, 2), async () => {
      throw new Error('Network error. Please check your connection.');
    });

    expect(toasts()).toEqual([expect.objectContaining({ variant: 'warning', title: 'Deleted 1 items, 1 failed' })]);
    // r1 was deleted; r2's delete failed, so it stays; r3 was never selected.
    expect(store().tableData?.items.map((i) => i.id)).toEqual(['r2', 'r3']);
    expect(store().allRecordsTableData?.items.map((i) => i.id)).toEqual(['r2']);
    expect(store().nodes.map((n) => n.id)).toEqual(['r2']);
  });
});

describe('re-indexing a selection', () => {
  it('uses the endpoint that matches each item and reports the tally', async () => {
    const api = fakeApi({
      [`POST ${KB}/reindex/record/r1`]: { status: 200 },
      [`POST ${KB}/reindex/record-group/g1`]: { status: 500 },
    });
    store().selectItem('r1');
    const refresh = vi.fn(async () => {});

    await store().bulkReindexSelected(
      [
        { id: 'r1', name: 'a.pdf', nodeType: 'record' },
        { id: 'g1', name: 'Team docs', nodeType: 'recordGroup' },
      ],
      refresh,
    );

    expect(api.sent.map((r) => r.url).sort()).toEqual([`${KB}/reindex/record-group/g1`, `${KB}/reindex/record/r1`]);
    expect(toasts()).toEqual([expect.objectContaining({ variant: 'warning', title: 'Reindexed 1 items, 1 failed' })]);
    expect(store().selectedItems.size).toBe(0);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('does not say the reindex failed when only the refresh afterwards fails', async () => {
    fakeApi({ [`POST ${KB}/reindex/record/r1`]: { status: 200 } });
    await store().bulkReindexSelected([{ id: 'r1', name: 'a.pdf', nodeType: 'record' }], async () => {
      throw new Error('Network error. Please check your connection.');
    });
    expect(toasts()).toEqual([expect.objectContaining({ variant: 'success', title: 'Successfully reindexed 1 items' })]);
  });
});

describe('the sidebar child cache', () => {
  it('drops a deleted folder, its expansion, and a parent left with no children', () => {
    store().cacheNodeChildren('kb-1', [node('f1', { nodeType: 'folder' })]);
    store().cacheNodeChildren('f1', [node('r1')]);
    store().setNodeChildrenPagination('f1', { hasNext: true } as never);
    store().toggleFolderExpanded('kb-1');
    store().toggleFolderExpanded('f1');

    store().purgeDeletedIdsFromSidebarChildrenCaches(['f1', '']);

    expect(store().nodeChildrenCache.has('f1')).toBe(false);
    expect(store().nodeChildrenCache.has('kb-1')).toBe(false);
    expect(store().nodeChildrenPagination.has('f1')).toBe(false);
    expect(store().expandedFolders).toEqual({});
  });

  it('is a no-op for an empty list', () => {
    store().cacheNodeChildren('kb-1', [node('r1')]);
    store().purgeDeletedIdsFromSidebarChildrenCaches([]);
    expect(store().nodeChildrenCache.get('kb-1')).toHaveLength(1);
  });
});

describe('browsing state', () => {
  it('goes back to page 1 whenever what is listed changes', () => {
    store().setCollectionsPagination({ page: 3, limit: 50, totalItems: 500, totalPages: 10, hasNext: true, hasPrev: true });
    store().setSearchQuery('invoice');
    expect(store().collectionsPagination.page).toBe(1);
    store().setCollectionsPage(4);
    store().setSort({ field: 'name', order: 'asc' } as never);
    expect(store().collectionsPagination.page).toBe(1);
    store().setCollectionsPage(10);
    expect(store().collectionsPagination).toMatchObject({ hasNext: false, hasPrev: true });
    store().setCollectionsLimit(100);
    expect(store().collectionsPagination).toMatchObject({ page: 1, totalPages: 5, hasNext: true, hasPrev: false });
  });

  it('keeps the page when a filter is restored from the URL', () => {
    store().setCollectionsPage(2);
    store().hydrateFilter({ recordTypes: ['FILE'] } as never);
    expect(store().collectionsPagination.page).toBe(2);
    store().clearFilter();
    expect(store().filter).toEqual({});
    expect(store().collectionsPagination.page).toBe(1);
  });

  it('expanding a folder collapses its siblings and everything under them', () => {
    store().setCategorizedNodes({
      shared: [
        { id: 'a', children: [{ id: 'a1', children: [] }] },
        { id: 'b', children: [] },
      ],
      private: [],
    } as never);
    store().toggleFolderExpanded('a');
    store().toggleFolderExpanded('a1');
    store().expandFolderExclusive('b');
    expect(store().expandedFolders).toEqual({ b: true });
  });

  it('pages all records from the loaded list', () => {
    store().setAllRecordsLimit(2);
    store().setAllRecords([{ id: '1' }, { id: '2' }, { id: '3' }] as never);
    expect(store().allRecordsPagination).toMatchObject({ totalItems: 3, totalPages: 2, hasNext: true, hasPrev: false });
    store().setAllRecordsPage(2);
    expect(store().allRecordsPagination).toMatchObject({ hasNext: false, hasPrev: true });
    store().syncAllRecordsPaginationMeta({ totalItems: 9, totalPages: 5, hasNext: true, hasPrev: true });
    expect(store().allRecordsPagination).toMatchObject({ page: 2, limit: 2, totalItems: 9 });
  });

  it('selects, toggles and clears', () => {
    store().setItems([{ id: 'x' }, { id: 'y' }] as never);
    store().selectAllItems();
    store().toggleItemSelection('x');
    expect([...store().selectedItems]).toEqual(['y']);
    store().setCurrentFolderId('f9');
    expect(store().selectedItems.size).toBe(0);
    store().addNodes([node('n1')]);
    store().addNodes([node('n1'), node('n2')]);
    expect(store().nodes.map((n) => n.id)).toEqual(['n1', 'n2']);
  });
});
