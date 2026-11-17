import { describe, it, expect, vi, beforeEach } from 'vitest';

const client = vi.hoisted(() => ({ put: vi.fn() }));
vi.mock('@/lib/api/axios-instance', () => ({ apiClient: client }));

import { WebSearchApi } from '../api';

beforeEach(() => client.put.mockReset());

describe('WebSearchApi.setDefaultProvider', () => {
  it('hands back the warning when DuckDuckGo was saved while it refused the test search', async () => {
    client.put.mockResolvedValue({
      data: { status: 'success', warning: 'DuckDuckGo blocked the test search. It was saved anyway.' },
    });

    const result = await WebSearchApi.setDefaultProvider('duckduckgo');

    expect(result.warning).toMatch(/saved anyway/);
  });

  it('has no warning when the provider passed its check', async () => {
    client.put.mockResolvedValue({ data: { status: 'success' } });

    expect((await WebSearchApi.setDefaultProvider('serper-key')).warning).toBeUndefined();
  });

  it('fails with the server message otherwise', async () => {
    client.put.mockResolvedValue({ data: { status: 'error', message: 'No web search configuration found' } });

    await expect(WebSearchApi.setDefaultProvider('x')).rejects.toThrow('No web search configuration found');
  });
});
