import { describe, expect, it, vi } from 'vitest';

const apiClient = vi.hoisted(() => ({ post: vi.fn(), put: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient }));

import { McpServersApi } from '../api';

describe('McpServersApi saving an instance', () => {
  it('leaves the error to the form: no global toast on create or update', async () => {
    apiClient.post.mockResolvedValue({ data: { _id: 'a' } });
    apiClient.put.mockResolvedValue({ data: { _id: 'a' } });
    const payload = { name: 'n' } as never;

    await McpServersApi.createInstance(payload);
    await McpServersApi.updateInstance('a', payload);

    expect(apiClient.post.mock.calls[0][2]).toEqual({ suppressErrorToast: true });
    expect(apiClient.put.mock.calls[0][2]).toEqual({ suppressErrorToast: true });
  });
});
