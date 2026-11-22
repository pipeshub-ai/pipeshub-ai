import { describe, it, expect, vi, beforeEach } from 'vitest';

const get = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiClient: { get } }));

import { BotsApi } from '../api';

const page = (keys: string[], hasNext: boolean) => ({
  data: { agents: keys.map((key) => ({ _key: key, name: `Agent ${key}` })), pagination: { hasNext } },
});

describe('BotsApi.getAgents', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('asks for the largest page, because the endpoint pages at 20 and would hide a linked agent', async () => {
    get.mockResolvedValueOnce(page(['a'], false));

    await BotsApi.getAgents();

    expect(get).toHaveBeenCalledWith('/api/v1/agents', { params: { page: 1, limit: 200 } });
  });

  it('says the list is complete once the last page has arrived', async () => {
    get.mockResolvedValueOnce(page(['a'], false));

    expect((await BotsApi.getAgents()).complete).toBe(true);
  });

  it('follows the pages until the last one and returns every agent', async () => {
    get.mockResolvedValueOnce(page(['a', 'b'], true)).mockResolvedValueOnce(page(['c'], false));

    const { agents } = await BotsApi.getAgents();

    expect(get).toHaveBeenCalledTimes(2);
    expect(get.mock.calls[1]?.[1]).toEqual({ params: { page: 2, limit: 200 } });
    expect(agents).toEqual([
      { id: 'a', name: 'Agent a' },
      { id: 'b', name: 'Agent b' },
      { id: 'c', name: 'Agent c' },
    ]);
  });

  it('names an agent by its key when it has no name', async () => {
    get.mockResolvedValueOnce({ data: { agents: [{ _key: 'k1' }], pagination: { hasNext: false } } });

    expect((await BotsApi.getAgents()).agents).toEqual([{ id: 'k1', name: 'k1' }]);
  });

  it('stops at the page cap if the server keeps saying there is more', async () => {
    get.mockResolvedValue(page(['a'], true));

    const result = await BotsApi.getAgents();

    expect(get).toHaveBeenCalledTimes(25);
    expect(result.complete).toBe(false);
  });

  it('returns nothing for an empty or malformed response', async () => {
    get.mockResolvedValueOnce({ data: {} });

    const result = await BotsApi.getAgents();

    expect(result.agents).toEqual([]);
    expect(result.complete).toBe(false);
  });

  it('does not call the list complete when a page carries no pagination metadata', async () => {
    get.mockResolvedValueOnce({ data: { agents: [{ _key: 'a', name: 'Agent a' }] } });

    const result = await BotsApi.getAgents();

    expect(get).toHaveBeenCalledTimes(1);
    expect(result.agents).toEqual([{ id: 'a', name: 'Agent a' }]);
    expect(result.complete).toBe(false);
  });

  it('does not call the list complete when hasNext is not a boolean', async () => {
    get.mockResolvedValueOnce({ data: { agents: [{ _key: 'a' }], pagination: { hasNext: 'no' } } });

    expect((await BotsApi.getAgents()).complete).toBe(false);
  });
});
