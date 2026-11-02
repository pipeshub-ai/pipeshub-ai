import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useKnowledgeBaseStore } from '../store';
import { resetKnowledgeBaseSession } from '../utils/sidebar-session';
import { openFolderChildren, reloadOpenFoldersUnder, seedChildrenFromListing } from '../utils/folder-children';
import { collection, hubChildrenFake, hubNode, hubResponse } from './kb-page-harness';
import type { KnowledgeHubNode, KnowledgeHubQueryParams } from '../types';

const getNodeChildren = vi.hoisted(() => vi.fn());
vi.mock('../api', async () => {
  const { pagedByCursor } = await import('./kb-page-harness');
  return {
    KnowledgeHubApi: { getNodeChildren: pagedByCursor(getNodeChildren, 2) },
    forgetPendingNodeChildrenRequests: () => {},
  };
});

const ENGINEERING = collection('kb-eng', 'Engineering');
const TABLE_PARAMS: KnowledgeHubQueryParams = {
  limit: 50,
  include: 'counts,permissions,breadcrumbs,availableFilters',
  sortBy: 'updatedAt',
  sortOrder: 'desc',
};

const folder = (id: string, name: string) => hubNode({ id, name, nodeType: 'folder', parentId: 'kb-eng' });
const kb = () => useKnowledgeBaseStore.getState();
const childIds = (id: string) => kb().nodeChildrenCache.get(id)?.map((n) => n.id);

function seed(items: KnowledgeHubNode[], params = TABLE_PARAMS, hasNext = false) {
  const response = hubResponse(items);
  seedChildrenFromListing('kb-eng', 'app', params, {
    ...response,
    pagination: { ...response.pagination!, hasNext, nextCursor: hasNext ? 'p2' : null },
  });
}

beforeEach(() => {
  resetKnowledgeBaseSession();
  getNodeChildren.mockReset();
  kb().setAppNodes([ENGINEERING]);
  kb().setNodes([ENGINEERING]);
  kb().setCategorizedNodes({ shared: [], private: [] });
});

describe('seedChildrenFromListing', () => {
  it('fills the tree from a complete table listing, so opening the collection asks for nothing', async () => {
    seed([folder('f-b', 'Beta'), folder('f-a', 'Alpha')]);

    await openFolderChildren('kb-eng', 'app');

    expect(childIds('kb-eng')).toEqual(['f-a', 'f-b']);
    expect(getNodeChildren).not.toHaveBeenCalled();
  });

  it('keeps exactly what the server lists as containers, and adds no record to the tree', () => {
    const withChildren = hubNode({ id: 'r-thread', name: 'Thread', nodeType: 'record', hasChildren: true });
    const plainRecord = hubNode({ id: 'r-doc', name: 'Doc', nodeType: 'record', hasChildren: false });
    const group = hubNode({ id: 'g-1', name: 'Group', nodeType: 'recordGroup' });
    const app = hubNode({ id: 'a-1', name: 'App', nodeType: 'app' });

    seed([plainRecord, withChildren, group, folder('f-a', 'Folder'), app]);

    expect(childIds('kb-eng')).toEqual(['a-1', 'f-a', 'g-1', 'r-thread']);
    expect(kb().nodes.some((n) => n.id === 'r-doc')).toBe(false);
  });

  it('orders rows the way the server does: lower-cased by code unit, nameless last, then id', () => {
    const nameless = { ...folder('f-none', 'x'), name: null as unknown as string };
    seed([
      folder('f-z', 'zeta'),
      folder('f-e', 'Éclair'),
      nameless,
      folder('f-u', '_archive'),
      folder('f-h', '#ops'),
      folder('f-a2', 'alpha'),
      folder('f-a1', 'Alpha'),
    ]);

    expect(childIds('kb-eng')).toEqual(['f-h', 'f-u', 'f-a1', 'f-a2', 'f-z', 'f-e', 'f-none']);
  });

  it.each([
    ['a search', { q: 'spec' }],
    ['a filter', { nodeTypes: 'folder' }],
    ['a later page', { cursor: 'p2' }],
    ['a page number', { page: 2 }],
    ['an unknown parameter', { somethingNew: 'x' }],
  ])('does nothing for a listing narrowed by %s', (_label, extra) => {
    seed([folder('f-a', 'Alpha')], { ...TABLE_PARAMS, ...extra } as KnowledgeHubQueryParams);

    expect(kb().nodeChildrenCache.has('kb-eng')).toBe(false);
  });

  it('does nothing when the table has more children than it showed', () => {
    seed([folder('f-a', 'Alpha')], TABLE_PARAMS, true);

    expect(kb().nodeChildrenCache.has('kb-eng')).toBe(false);
  });

  it('replaces a complete list with the fresher one, but never a list with pages still to load', () => {
    seed([folder('f-a', 'Alpha')]);
    seed([folder('f-a', 'Alpha'), folder('f-b', 'Beta')]);
    expect(childIds('kb-eng')).toEqual(['f-a', 'f-b']);

    kb().setNodeChildrenPagination('kb-eng', { hasNext: true, nextCursor: 'p2', nodeType: 'app', pagesLoaded: 1 });
    seed([folder('f-c', 'Gamma')]);
    expect(childIds('kb-eng')).toEqual(['f-a', 'f-b']);
  });

  it('records its pages in tree-page size, so a reload after a rename keeps every row', async () => {
    const folders = Array.from({ length: 35 }, (_, i) => folder(`f-${String(i).padStart(2, '0')}`, `Folder ${String(i).padStart(2, '0')}`));
    seed(folders);
    expect(kb().nodeChildrenPagination.get('kb-eng')).toEqual(
      expect.objectContaining({ hasNext: false, pagesLoaded: 2 }),
    );
    getNodeChildren.mockImplementation(hubChildrenFake(() => ({ 'kb-eng': folders })));
    useKnowledgeBaseStore.setState((s) => ({ expandedFolders: { ...s.expandedFolders, 'kb-eng': true } }));

    await reloadOpenFoldersUnder(['kb-eng']);

    expect(childIds('kb-eng')).toHaveLength(35);
  });

  it('keeps the tree type of the node for later reads', () => {
    kb().addNodes([hubNode({ id: 'f-x', name: 'X', nodeType: 'folder' })]);
    const response = hubResponse([hubNode({ id: 'f-y', name: 'Y', nodeType: 'folder', parentId: 'f-x' })]);

    seedChildrenFromListing('f-x', 'kb' as never, TABLE_PARAMS, response);

    expect(kb().nodeChildrenPagination.get('f-x')?.nodeType).toBe('folder');
  });
});
