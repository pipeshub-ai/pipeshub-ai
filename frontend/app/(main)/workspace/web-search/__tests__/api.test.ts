import { describe, it, expect, vi, beforeEach } from 'vitest';

const client = vi.hoisted(() => ({ put: vi.fn(), get: vi.fn() }));
vi.mock('@/lib/api/axios-instance', () => ({ apiClient: client }));

import { WebSearchApi } from '../api';

beforeEach(() => {
  client.put.mockReset();
  client.get.mockReset();
});

describe('WebSearchApi — a failed read or save', () => {
  it('a read that fails gives no settings, so defaults are never offered for editing', async () => {
    client.get.mockRejectedValue(new Error('Network Error'));
    expect((await WebSearchApi.getConfig()).settings).toBeUndefined();
  });

  it('an unsuccessful read gives no settings either', async () => {
    client.get.mockResolvedValue({ data: { status: 'error', message: 'nope' } });
    expect((await WebSearchApi.getConfig()).settings).toBeUndefined();
  });

  it('a refused save carries the server message, or none for the page to translate', async () => {
    client.put.mockResolvedValue({ data: { status: 'error', message: 'Only admins can change this' } });
    await expect(WebSearchApi.updateSettings({ includeImages: true, maxImages: 3 })).rejects.toThrow('Only admins can change this');
    client.put.mockResolvedValue({ data: { status: 'error' } });
    await expect(WebSearchApi.updateSettings({ includeImages: true, maxImages: 3 })).rejects.toThrow(/^$/);
  });
});

describe('WebSearchApi.getConfig — image settings', () => {
  it.each([
    [{ includeImages: true, maxImages: 7 }, { includeImages: true, maxImages: 7 }],
    // Stored up to 500, but no more than 50 are read from a page: the editor shows what applies.
    [{ includeImages: true, maxImages: 500 }, { includeImages: true, maxImages: 50 }],
    [{ includeImages: true, maxImages: 51 }, { includeImages: true, maxImages: 50 }],
    [{ includeImages: true, maxImages: 501 }, { includeImages: true, maxImages: 3 }],
    [{ includeImages: 'yes', maxImages: 'many' }, { includeImages: false, maxImages: 3 }],
    [undefined, { includeImages: false, maxImages: 3 }],
  ])('%j reads as %j', async (stored, expected) => {
    client.get.mockResolvedValue({ data: { status: 'success', providers: [], settings: stored } });

    expect((await WebSearchApi.getConfig()).settings).toEqual(expected);
  });
});

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
