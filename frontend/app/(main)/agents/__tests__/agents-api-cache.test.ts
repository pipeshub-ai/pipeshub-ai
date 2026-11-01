import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';
import { AgentsApi } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
}));
vi.mock('@/lib/api/swr-cache', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/api/swr-cache')>()),
  revalidateSwrNamespace: vi.fn(),
}));

const api = vi.mocked(apiClient);
const revalidate = vi.mocked(revalidateSwrNamespace);
const AGENT = { _key: 'a1', name: 'Helper' };

describe('agent writes refresh the cached agent lists', () => {
  beforeEach(() => vi.clearAllMocks());

  it.each([
    ['create', () => {
      api.post.mockResolvedValue({ data: { agent: AGENT } });
      return AgentsApi.createAgent({ name: 'Helper' } as never);
    }],
    ['update', () => {
      api.put.mockResolvedValue({ data: { agent: AGENT } });
      return AgentsApi.updateAgent('a1', { name: 'Helper' });
    }],
    ['delete', () => {
      api.delete.mockResolvedValue({ data: {} });
      return AgentsApi.deleteAgent('a1');
    }],
  ])('after a successful %s', async (_name, write) => {
    await write();

    expect(revalidate).toHaveBeenCalledWith(SWR_NS.agentLists);
  });

  it('not after a failed write, since nothing changed', async () => {
    api.delete.mockRejectedValue(new Error('500'));

    await expect(AgentsApi.deleteAgent('a1')).rejects.toThrow('500');

    expect(revalidate).not.toHaveBeenCalled();
  });

  it('lists agents quietly when the caller shows its own error', async () => {
    api.get.mockResolvedValue({ data: { agents: [] } });

    await AgentsApi.getAgents({ page: 1, limit: 20 }, { suppressErrorToast: true });

    expect(api.get).toHaveBeenCalledWith(expect.any(String), {
      params: { page: 1, limit: 20 },
      suppressErrorToast: true,
    });
  });
});
