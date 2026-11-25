import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { AgentsApi } from '../api';
import { catalogItems, catalogSteps } from '../agent-builder/hooks/paged-catalog';

const apps = catalogSteps(AgentsApi.knowledgeHubAppPage);
/** Every connected app, as a group dropped whole reads them: the first page, then the rest. */
const allApps = async () => catalogItems(await apps.rest(await apps.first()), (node) => node.id);

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

describe('AgentsApi.knowledgeHubAppPage (the builder\'s connected apps catalog)', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('asks for the first page without a cursor, and keeps the apps-only scope', async () => {
    mockedGet.mockResolvedValueOnce(page(['a1'], null));

    await allApps();

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

    const nodes = await allApps();

    expect(nodes.map((n) => n.id)).toEqual(['a1', 'a2', 'a3', 'a4']);
    expect(queryOf(1).cursor).toBe('c1');
    expect(queryOf(2).cursor).toBe('c2');
  });

  it('stops at the missing next cursor even when the page is full', async () => {
    // hasNext:false with no cursor is the end — a full page must not imply more.
    mockedGet.mockResolvedValueOnce(page(['a1'], null));

    const nodes = await allApps();

    expect(nodes).toHaveLength(1);
    expect(mockedGet).toHaveBeenCalledTimes(1);
  });

  it('pages past the old 100-page cap', async () => {
    const PAGES = 130;
    for (let i = 0; i < PAGES; i += 1) {
      const last = i === PAGES - 1;
      mockedGet.mockResolvedValueOnce(page([`a${i}`], last ? null : `c${i}`));
    }

    const nodes = await allApps();

    // A page cap would truncate the palette silently.
    expect(nodes).toHaveLength(PAGES);
    expect(mockedGet).toHaveBeenCalledTimes(PAGES);
  });

  it('stops instead of spinning when the server repeats a cursor', async () => {
    mockedGet.mockResolvedValue(page(['a1'], 'same-cursor'));

    const nodes = await allApps();

    expect(mockedGet.mock.calls.length).toBeLessThanOrEqual(2);
    expect(nodes.length).toBeLessThanOrEqual(2);
  });

  it('R30-LAT-FU-1: the builder load reads the first page only, and a search asks the server with q', async () => {
    mockedGet.mockResolvedValueOnce(page(['a1', 'a2'], 'c1')).mockResolvedValueOnce(page(['a450'], null));

    const first = await apps.first();
    expect(catalogItems(first, (n) => n.id).map((n) => n.id)).toEqual(['a1', 'a2']);
    expect(mockedGet).toHaveBeenCalledTimes(1);

    const found = await apps.search(first, 'a450');
    expect(queryOf(1).q).toBe('a450');
    expect(queryOf(1)).not.toHaveProperty('cursor');
    expect(catalogItems(found, (n) => n.id).map((n) => n.id)).toEqual(['a1', 'a2', 'a450']);
    expect(catalogItems(await apps.search(found, ''), (n) => n.id).map((n) => n.id)).toEqual(['a1', 'a2']);
  });

  it('drops malformed rows that carry no id', async () => {
    mockedGet.mockResolvedValueOnce({
      data: {
        items: [{ id: 'a1', name: 'a1' }, { name: 'no id' }],
        pagination: { limit: 100, totalItems: 2, hasNext: false, hasPrev: false, nextCursor: null },
      },
    });

    const nodes = await allApps();

    expect(nodes.map((n) => n.id)).toEqual(['a1']);
  });
});

describe('AgentsApi.getAgent', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockResolvedValue({ data: { agent: { _key: 'agent-7', name: 'Support Bot', toolsets: [] } } });
  });

  it('lets a failed lookup raise the global error toast by default', async () => {
    await AgentsApi.getAgent('agent-7');

    expect(mockedGet).toHaveBeenCalledWith('/api/v1/agents/agent-7', undefined);
  });

  it('keeps a failed lookup from raising that toast when the caller asks for quiet', async () => {
    await AgentsApi.getAgent('agent-7', { quiet: true });

    expect(mockedGet).toHaveBeenCalledWith('/api/v1/agents/agent-7', { suppressErrorToast: true });
  });
});

describe("AgentsApi.knowledgeBasePage (the builder's collections catalog, R30-LAT-FU-1)", () => {
  const collections = catalogSteps(AgentsApi.knowledgeBasePage);
  const kbPage = (ids: string[], pagination?: Record<string, unknown>) => ({
    data: { knowledgeBases: ids.map((id) => ({ id, name: id, connectorId: id })), ...(pagination ? { pagination } : {}) },
  });

  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('reads one page of 100 on load and the next on Show more, keeping the count the server gives', async () => {
    mockedGet
      .mockResolvedValueOnce(kbPage(['k1'], { page: 1, limit: 100, totalCount: 250, hasNext: true }))
      .mockResolvedValueOnce(kbPage(['k2'], { page: 2, limit: 100, totalCount: 250, hasNext: false }));
    const first = await collections.first();
    expect(queryOf(0)).toEqual({ page: 1, limit: 100 });
    expect(first.total).toBe(250);
    expect(first.next).toBe(2);
    const more = await collections.more(first);
    expect(queryOf(1)).toEqual({ page: 2, limit: 100 });
    expect(more.pages.map((kb) => kb.id)).toEqual(['k1', 'k2']);
    expect(more.next).toBeNull();
    expect(await collections.more(more)).toBe(more);
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('searches by name on the server, and reads a full page from a server without pagination as maybe more', async () => {
    mockedGet
      .mockResolvedValueOnce(kbPage(Array.from({ length: 100 }, (_, i) => `k${String(i)}`)))
      .mockResolvedValueOnce(kbPage(['k450'], { page: 1, limit: 100, totalCount: 1, hasNext: false }));
    const first = await collections.first();
    expect(first.next).toBe(2);
    const found = await collections.search(first, 'k450');
    expect(queryOf(1)).toEqual({ page: 1, limit: 100, search: 'k450' });
    expect(found.hits.map((kb) => kb.id)).toEqual(['k450']);
    expect(found.hitsComplete).toBe(true);
  });
});
