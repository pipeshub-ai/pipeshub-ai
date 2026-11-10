import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';
import { UsersApi } from '../api';
import { ProfileApi } from '../../profile/api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/api/swr-cache', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/api/swr-cache')>()),
  revalidateSwrNamespace: vi.fn(),
}));

const api = vi.mocked(apiClient);
const revalidate = vi.mocked(revalidateSwrNamespace);

describe('user writes refresh the names and pictures shown on cards', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.put.mockResolvedValue({ data: {} });
    api.delete.mockResolvedValue({ data: null });
    api.get.mockResolvedValue({ data: new ArrayBuffer(0), headers: { 'content-type': 'image/png' } });
  });

  it.each([
    ['an admin edits a user', () => UsersApi.updateUser('u1', { fullName: 'Ada King' })],
    ['an admin removes a user', () => UsersApi.deleteUser('u1')],
    ['an admin removes several users', () => UsersApi.bulkRemoveUsers(['u1', 'u2'])],
    ['an admin unblocks a user', () => UsersApi.unblockUser('u1')],
    ['I edit my name', () => ProfileApi.updateUser('me', { fullName: 'Ada King' })],
    ['I change my picture', () => ProfileApi.uploadAvatar(new File(['x'], 'me.png', { type: 'image/png' }))],
    ['I remove my picture', () => ProfileApi.deleteAvatar()],
  ])('after %s', async (_name, write) => {
    await write();

    expect(revalidate).toHaveBeenCalledWith(SWR_NS.userDirectory);
  });

  it('not after a failed write', async () => {
    api.put.mockRejectedValue(new Error('500'));

    await expect(UsersApi.updateUser('u1', { fullName: 'Ada King' })).rejects.toThrow('500');

    expect(revalidate).not.toHaveBeenCalled();
  });

  it('not when every removal in a bulk remove failed', async () => {
    api.delete.mockRejectedValue(new Error('500'));

    await UsersApi.bulkRemoveUsers(['u1', 'u2']);

    expect(revalidate).not.toHaveBeenCalled();
  });
});
