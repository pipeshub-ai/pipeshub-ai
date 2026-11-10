import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { useUserStore } from '@/lib/store/user-store';
import { ProfileApi } from '../api';

vi.mock('@/lib/api', () => ({ apiClient: { put: vi.fn() } }));

const put = vi.mocked(apiClient.put);

function signedInAs(userId: string) {
  useUserStore.setState({
    isInitialized: true,
    profile: {
      userId, firstName: null, lastName: null, fullName: 'A User', email: 'a@acme.test',
      isAdmin: false, avatarUrl: null, hasLoggedIn: true, designation: 'Engineer',
    },
  });
}

describe('ProfileApi.updateUser keeps the signed-in profile current', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    put.mockResolvedValue({ data: {} });
    signedInAs('me');
  });

  it('a profile edit updates the cached name and designation', async () => {
    await ProfileApi.updateUser('me', { fullName: 'New Name', designation: 'Lead' });

    expect(useUserStore.getState().profile).toMatchObject({ fullName: 'New Name', designation: 'Lead', isAdmin: false });
  });

  it('an admin changing their own role on the Users page updates the cached role', async () => {
    await ProfileApi.updateUser('me', { role: 'admin' }, { suppressErrorToast: true });
    expect(useUserStore.getState().profile?.isAdmin).toBe(true);

    await ProfileApi.updateUser('me', { role: 'member' }, { suppressErrorToast: true });
    expect(useUserStore.getState().profile?.isAdmin).toBe(false);
  });

  it("changing someone else's role leaves the signed-in profile alone", async () => {
    await ProfileApi.updateUser('someone-else', { role: 'admin', fullName: 'Other' });

    expect(useUserStore.getState().profile).toMatchObject({ fullName: 'A User', isAdmin: false });
  });

  it('a failed update leaves the cached profile unchanged', async () => {
    put.mockRejectedValueOnce(new Error('500'));

    await expect(ProfileApi.updateUser('me', { fullName: 'New Name', role: 'admin' })).rejects.toThrow('500');

    expect(useUserStore.getState().profile).toMatchObject({ fullName: 'A User', isAdmin: false });
  });
});
