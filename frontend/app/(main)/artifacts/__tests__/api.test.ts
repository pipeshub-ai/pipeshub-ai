import { afterEach, describe, expect, it, vi } from 'vitest';

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient: client }));

import { ArtifactsApi } from '../api';

afterEach(() => vi.clearAllMocks());

describe('ArtifactsApi error toasts', () => {
  it('leaves reporting a missing artifact or a failed share to the caller', async () => {
    client.get.mockResolvedValue({ data: {} });
    client.post.mockResolvedValue({ data: {} });

    await ArtifactsApi.get('a1');
    await ArtifactsApi.share('a1', ['u1']);
    await ArtifactsApi.unshare('a1', ['u1']);

    expect(client.get).toHaveBeenCalledWith('/api/v1/artifacts/a1', { suppressErrorToast: true });
    expect(client.post).toHaveBeenCalledWith('/api/v1/artifacts/a1/share', { userIds: ['u1'] }, { suppressErrorToast: true });
    expect(client.post).toHaveBeenCalledWith('/api/v1/artifacts/a1/unshare', { userIds: ['u1'] }, { suppressErrorToast: true });
  });
});
