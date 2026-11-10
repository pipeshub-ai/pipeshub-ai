import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { refreshSwrKey, revalidateSwrNamespace, SWR_NS, writeSwrKey } from '@/lib/api/swr-cache';
import { clearSessionCache } from '@/lib/api/session-cache';
import { AgentsApi, agentDetailKey } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
}));
vi.mock('@/lib/api/swr-cache', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/api/swr-cache')>()),
  revalidateSwrNamespace: vi.fn(),
  refreshSwrKey: vi.fn(),
  writeSwrKey: vi.fn(),
}));

const api = vi.mocked(apiClient);
const revalidate = vi.mocked(revalidateSwrNamespace);
const refreshKey = vi.mocked(refreshSwrKey);
const writeKey = vi.mocked(writeSwrKey);
const AGENT = { _key: 'a1', name: 'Helper' };

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  let reject: (error: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe('concurrent reads of one agent share a request', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearSessionCache();
  });

  it('quiet reads made while one is in flight make one request', async () => {
    const answer = deferred<{ data: unknown }>();
    api.get.mockReturnValue(answer.promise as never);

    const reads = [AgentsApi.getAgent('a1', { quiet: true }), AgentsApi.getAgent('a1', { quiet: true })];
    answer.resolve({ data: { agent: AGENT } });

    const [first, second] = await Promise.all(reads);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith('/api/v1/agents/a1', { suppressErrorToast: true });
    expect(first.agent).toEqual(AGENT);
    expect(second).toBe(first);
  });

  it('keeps nothing once a request settled: the next read asks again', async () => {
    api.get.mockResolvedValue({ data: { agent: AGENT } });

    await AgentsApi.getAgent('a1', { quiet: true });
    await AgentsApi.getAgent('a1', { quiet: true });

    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('a failed shared request fails every reader, and the next read retries', async () => {
    const answer = deferred<{ data: unknown }>();
    api.get.mockReturnValueOnce(answer.promise as never);

    const reads = [AgentsApi.getAgent('a1', { quiet: true }), AgentsApi.getAgent('a1', { quiet: true })];
    answer.reject(new Error('500'));
    await expect(reads[0]).rejects.toThrow('500');
    await expect(reads[1]).rejects.toThrow('500');

    api.get.mockResolvedValue({ data: { agent: AGENT } });
    await expect(AgentsApi.getAgent('a1', { quiet: true })).resolves.toMatchObject({ agent: AGENT });
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('does not merge a quiet read with one that shows its own error toast', async () => {
    api.get.mockReturnValue(new Promise(() => {}) as never);

    void AgentsApi.getAgent('a1', { quiet: true });
    void AgentsApi.getAgent('a1');

    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('a read after a save does not join a request that started before it', async () => {
    api.get.mockReturnValue(new Promise(() => {}) as never);
    void AgentsApi.getAgent('a1', { quiet: true });
    api.put.mockResolvedValue({ data: { agent: AGENT } });

    await AgentsApi.updateAgent('a1', { name: 'Helper' });
    void AgentsApi.getAgent('a1', { quiet: true });

    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('a request that settles late does not drop a newer one for the same agent', async () => {
    const old = deferred<{ data: unknown }>();
    api.get.mockReturnValueOnce(old.promise as never);
    void AgentsApi.getAgent('a1', { quiet: true }).catch(() => {});
    api.put.mockResolvedValue({ data: { agent: AGENT } });
    await AgentsApi.updateAgent('a1', { name: 'Helper' });

    api.get.mockReturnValueOnce(new Promise(() => {}) as never);
    void AgentsApi.getAgent('a1', { quiet: true });
    old.resolve({ data: { agent: AGENT } });
    await new Promise((r) => setTimeout(r, 0));
    void AgentsApi.getAgent('a1', { quiet: true });

    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('session end lets the next user ask again instead of waiting on the last session', async () => {
    // A request from the old session may never settle.
    api.get.mockReturnValueOnce(new Promise(() => {}) as never);
    const stuck = AgentsApi.getAgent('a1', { quiet: true });

    clearSessionCache();
    api.get.mockResolvedValue({ data: { agent: AGENT } });

    await expect(AgentsApi.getAgent('a1', { quiet: true })).resolves.toMatchObject({ agent: AGENT });
    expect(api.get).toHaveBeenCalledTimes(2);
    void stuck;
  });
});

describe('agent writes update the cached agent detail', () => {
  beforeEach(() => vi.clearAllMocks());

  it('a save drops that agent so its next reader asks again', async () => {
    api.put.mockResolvedValue({ data: { agent: AGENT } });

    await AgentsApi.updateAgent('a1', { name: 'Helper' });

    expect(refreshKey).toHaveBeenCalledTimes(1);
    expect(refreshKey).toHaveBeenCalledWith(agentDetailKey('a1'));
  });

  it('a delete marks the agent gone without asking the server', async () => {
    api.delete.mockResolvedValue({ data: {} });

    await AgentsApi.deleteAgent('a1');

    expect(writeKey).toHaveBeenCalledWith(agentDetailKey('a1'), { agent: null, toolFullNames: [] });
    expect(refreshKey).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it('a failed save or delete changes nothing', async () => {
    api.put.mockRejectedValue(new Error('500'));
    api.delete.mockRejectedValue(new Error('500'));

    await expect(AgentsApi.updateAgent('a1', {})).rejects.toThrow('500');
    await expect(AgentsApi.deleteAgent('a1')).rejects.toThrow('500');

    expect(refreshKey).not.toHaveBeenCalled();
    expect(writeKey).not.toHaveBeenCalled();
  });
});

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

  it('after a create whose answer lacks the agent, since it may exist anyway', async () => {
    api.post.mockResolvedValue({ data: { status: 'ok' } });

    await expect(AgentsApi.createAgent({ name: 'Helper' } as never)).rejects.toThrow('Create agent failed');

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
