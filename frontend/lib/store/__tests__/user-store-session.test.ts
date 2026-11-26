/**
 * The signed-in person's profile (name, email, admin role) is kept in memory and
 * in localStorage. The profile initializer is unmounted before it sees the
 * sign-out, so the store clears itself at session end; otherwise the next
 * person on the desktop app would inherit the name and the admin role.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const get = vi.fn();
vi.mock('@/lib/api', () => ({ apiClient: { get: (...args: unknown[]) => get(...args) } }));
vi.mock('@/lib/utils/jwt', () => ({ getUserIdFromToken: () => 'user-b' }));
vi.mock('@/app/(main)/workspace/profile/api', () => ({
  ProfileApi: { getAvatar: () => Promise.resolve(null) },
}));

import { clearSessionCache } from '@/lib/api/session-cache';
import { useInitializeUserProfile } from '@/lib/hooks/use-initialize-user-profile';
import { useUserStore, type UserProfile } from '../user-store';

const adminA: UserProfile = {
  userId: 'user-a',
  firstName: 'Ada',
  lastName: 'Admin',
  fullName: 'Ada Admin',
  email: 'ada@example.com',
  isAdmin: true,
  avatarUrl: 'blob:avatar-a',
  hasLoggedIn: true,
};

function signedInAsA() {
  const store = useUserStore.getState();
  store.setProfile(adminA);
  store.setInitialized(true);
}

beforeEach(() => {
  get.mockReset();
  localStorage.clear();
  useUserStore.getState().clearProfile();
  URL.revokeObjectURL = vi.fn();
});

describe('the user profile at session end', () => {
  it('is cleared in memory and in localStorage', () => {
    signedInAsA();
    expect(localStorage.getItem('user-profile-storage')).toContain('Ada Admin');

    clearSessionCache();

    expect(useUserStore.getState().profile).toBeNull();
    expect(useUserStore.getState().isInitialized).toBe(false);
    expect(JSON.parse(localStorage.getItem('user-profile-storage') ?? '{}').state.profile).toBeNull();
  });

  it('releases the avatar image', () => {
    signedInAsA();
    clearSessionCache();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:avatar-a');
  });

  it("is loaded for user B after A signs out, instead of keeping A's name and admin role", async () => {
    signedInAsA();
    clearSessionCache();
    get.mockImplementation((url: string) =>
      Promise.resolve({ data: url.startsWith('/api/v1/userGroups') ? [] : { fullName: 'Bob', role: 'member' } }),
    );

    const { result } = renderHook(() => useInitializeUserProfile());
    await act(async () => {
      await result.current.initialize(false);
    });

    expect(get).toHaveBeenCalledWith('/api/v1/users/user-b');
    expect(useUserStore.getState().profile?.fullName).toBe('Bob');
    expect(useUserStore.getState().profile?.isAdmin).toBe(false);
  });
});
