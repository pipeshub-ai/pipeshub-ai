import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { AgentsApi } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
  streamSSERequest: vi.fn(),
}));

const mockedGet = vi.mocked(apiClient.get);

/** One page of the knowledge-hub nodes response. */
function page(ids: string[], nextCursor: string | null) {
  return {
    data: {
      items: ids.map((id) => ({ id, name: id })),
      pagination: { limit: 100, totalItems: ids.length, hasNext: Boolean(nextCursor), hasPrev: false, nextCursor },
    },
  };
}

/** The query params of the nth (0-based) request the code made. */
function queryOf(call: number): Record<string, unknown> {
  return (mockedGet.mock.calls[call][1] as { params: Record<string, unknown> }).params;
}

describe('AgentsApi.getAllKnowledgeHubAppNodes', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('asks for the first page without a cursor, and keeps the apps-only scope', async () => {
    mockedGet.mockResolvedValueOnce(page(['a1'], null));

    await AgentsApi.getAllKnowledgeHubAppNodes();

    const q = queryOf(0);
    // A cursor the server did not issue is a 400, so the first page sends none.
    expect(q).not.toHaveProperty('cursor');
    expect(q).not.toHaveProperty('page');
    expect(q.origins).toBe('CONNECTOR');
    expect(q.flattened).toBe(false);
  });

  it('follows the next cursor across pages and returns every node once', async () => {
    mockedGet
      .mockResolvedValueOnce(page(['a1', 'a2'], 'c1'))
      .mockResolvedValueOnce(page(['a3'], 'c2'))
      .mockResolvedValueOnce(page(['a4'], null));

    const nodes = await AgentsApi.getAllKnowledgeHubAppNodes();

    expect(nodes.map((n) => n.id)).toEqual(['a1', 'a2', 'a3', 'a4']);
    expect(queryOf(1).cursor).toBe('c1');
    expect(queryOf(2).cursor).toBe('c2');
  });

  it('stops at the missing next cursor even when the page is full', async () => {
    // hasNext:false with no cursor is the end — a full page must not imply more.
    mockedGet.mockResolvedValueOnce(page(['a1'], null));

    const nodes = await AgentsApi.getAllKnowledgeHubAppNodes();

    expect(nodes).toHaveLength(1);
    expect(mockedGet).toHaveBeenCalledTimes(1);
  });

  it('pages past the old 100-page cap', async () => {
    const PAGES = 130;
    for (let i = 0; i < PAGES; i += 1) {
      const last = i === PAGES - 1;
      mockedGet.mockResolvedValueOnce(page([`a${i}`], last ? null : `c${i}`));
    }

    const nodes = await AgentsApi.getAllKnowledgeHubAppNodes();

    // A page cap would truncate the palette silently.
    expect(nodes).toHaveLength(PAGES);
    expect(mockedGet).toHaveBeenCalledTimes(PAGES);
  });

  it('stops instead of spinning when the server repeats a cursor', async () => {
    mockedGet.mockResolvedValue(page(['a1'], 'same-cursor'));

    const nodes = await AgentsApi.getAllKnowledgeHubAppNodes();

    expect(mockedGet.mock.calls.length).toBeLessThanOrEqual(2);
    expect(nodes.length).toBeLessThanOrEqual(2);
  });

  it('drops malformed rows that carry no id', async () => {
    mockedGet.mockResolvedValueOnce({
      data: {
        items: [{ id: 'a1', name: 'a1' }, { name: 'no id' }],
        pagination: { limit: 100, totalItems: 2, hasNext: false, hasPrev: false, nextCursor: null },
      },
    });

    const nodes = await AgentsApi.getAllKnowledgeHubAppNodes();

    expect(nodes.map((n) => n.id)).toEqual(['a1']);
  });
});
