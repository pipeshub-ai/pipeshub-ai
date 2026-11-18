import { beforeEach, describe, expect, it, vi } from 'vitest';

const post = vi.fn();
vi.mock('@/lib/api', () => ({ apiClient: { post: (...args: unknown[]) => post(...args) } }));

import { KnowledgeBaseApi } from '../api';

describe('the reindex request api.ts builds', () => {
  beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue({ data: {} });
  });

  it('a Force reindex puts force: true in the body', async () => {
    await KnowledgeBaseApi.reindexItem('rec-1', 0, undefined, true);

    const [url, body] = post.mock.calls[0];
    expect(url).toBe('/api/v1/knowledgeBase/reindex/record/rec-1');
    expect(body).toEqual({ depth: 0, force: true });
  });

  it('a plain reindex sends no force', async () => {
    await KnowledgeBaseApi.reindexItem('rec-1');
    await KnowledgeBaseApi.reindexItem('rec-1', 0, ['FAILED']);

    expect(post.mock.calls[0][1]).toEqual({ depth: 0 });
    expect(post.mock.calls[1][1]).toEqual({ depth: 0, statusFilters: ['FAILED'] });
  });
});
