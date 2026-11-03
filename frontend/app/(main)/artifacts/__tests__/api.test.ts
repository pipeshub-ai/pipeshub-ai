import { afterEach, describe, expect, it, vi } from 'vitest';
import { ErrorType, type ProcessedError } from '@/lib/api';

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiClient: client,
}));

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

function error(statusCode: number): ProcessedError {
  return { type: ErrorType.SERVER_ERROR, message: 'x', statusCode };
}

describe('ArtifactsApi.delete', () => {
  it('encodes the id into the URL', async () => {
    client.delete.mockResolvedValue({ status: 204 });

    await ArtifactsApi.delete('a/b c');

    expect(client.delete).toHaveBeenCalledWith('/api/v1/artifacts/a%2Fb%20c', expect.anything());
  });

  it('suppresses the global toast only for 404', async () => {
    client.delete.mockResolvedValue({ status: 204 });

    await ArtifactsApi.delete('a1');

    const { suppressErrorToast } = client.delete.mock.calls[0][1];
    expect(suppressErrorToast(error(404))).toBe(true);
    expect(suppressErrorToast(error(403))).toBe(false);
    expect(suppressErrorToast(error(503))).toBe(false);
  });
});
