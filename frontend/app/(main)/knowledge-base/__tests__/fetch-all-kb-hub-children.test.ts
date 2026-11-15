import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchAllKbAppContainerChildren } from '../utils/fetch-all-kb-hub-children';
import { hubNode, hubResponse } from './kb-page-harness';

const getNodeChildren = vi.hoisted(() => vi.fn());
vi.mock('../api', () => ({ KnowledgeHubApi: { getNodeChildren } }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { error: vi.fn() } }));

const page = (items: ReturnType<typeof hubNode>[], nextCursor: string | null) =>
  hubResponse(items, { pagination: { page: 1, limit: 100, totalItems: 2, totalPages: 1, hasNext: nextCursor !== null, hasPrev: false, nextCursor } });

describe('fetchAllKbAppContainerChildren', () => {
  beforeEach(() => getNodeChildren.mockReset());

  it('goes on from an empty page whose cursor moved', async () => {
    const a = hubNode({ id: 'a', name: 'A', nodeType: 'recordGroup' });
    const b = hubNode({ id: 'b', name: 'B', nodeType: 'recordGroup' });
    getNodeChildren.mockResolvedValueOnce(page([a], 'c1')).mockResolvedValueOnce(page([], 'c2')).mockResolvedValueOnce(page([b], null));
    expect((await fetchAllKbAppContainerChildren('app')).map((n) => n.id)).toEqual(['a', 'b']);
  });

  it('stops at an empty page that hands back the cursor it was asked with', async () => {
    const a = hubNode({ id: 'a', name: 'A', nodeType: 'recordGroup' });
    getNodeChildren.mockResolvedValueOnce(page([a], 'c1')).mockResolvedValue(page([], 'c1'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect((await fetchAllKbAppContainerChildren('app')).map((n) => n.id)).toEqual(['a']);
    expect(getNodeChildren).toHaveBeenCalledTimes(2);
  });
});
