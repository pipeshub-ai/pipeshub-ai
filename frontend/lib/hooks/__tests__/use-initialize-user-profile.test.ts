import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const get = vi.fn();
vi.mock('@/lib/api', () => ({ apiClient: { get: (...args: unknown[]) => get(...args) } }));
let tokenUserId: string | null = 'u1';
vi.mock('@/lib/utils/jwt', () => ({ getUserIdFromToken: () => tokenUserId }));
vi.mock('@/app/(main)/workspace/profile/api', () => ({
  ProfileApi: { getAvatar: () => Promise.resolve(null) },
}));

import { useInitializeUserProfile } from '../use-initialize-user-profile';
import { useUserStore } from '@/lib/store/user-store';

function answer(user: unknown, groups: unknown) {
  get.mockImplementation((url: string) => {
    const value = url.startsWith('/api/v1/userGroups') ? groups : user;
    return value instanceof Error ? Promise.reject(value) : Promise.resolve({ data: value });
  });
}

async function resolvedIsAdmin() {
  const { result } = renderHook(() => useInitializeUserProfile());
  await act(async () => {
    await result.current.initialize(true);
  });
  return useUserStore.getState().profile?.isAdmin;
}

beforeEach(() => {
  get.mockReset();
  tokenUserId = 'u1';
  useUserStore.getState().clearProfile();
});

describe('useInitializeUserProfile isAdmin', () => {
  it('reads the role from the user record', async () => {
    answer({ role: 'admin' }, [{ type: 'everyone' }]);
    expect(await resolvedIsAdmin()).toBe(true);
    answer({ role: 'member' }, [{ type: 'everyone' }]);
    expect(await resolvedIsAdmin()).toBe(false);
  });

  it('leaves the role unknown when the user record fails and no admin group says otherwise', async () => {
    answer(new Error('500'), [{ type: 'everyone' }]);
    expect(await resolvedIsAdmin()).toBeNull();
  });

  it('retries an unknown role on the next call, but not a known one', async () => {
    answer(new Error('500'), [{ type: 'everyone' }]);
    const { result } = renderHook(() => useInitializeUserProfile());
    await act(async () => {
      await result.current.initialize();
    });
    expect(useUserStore.getState().profile?.isAdmin).toBeNull();

    answer({ role: 'admin' }, [{ type: 'everyone' }]);
    await act(async () => {
      await result.current.initialize();
    });
    expect(useUserStore.getState().profile?.isAdmin).toBe(true);

    const calls = get.mock.calls.length;
    await act(async () => {
      await result.current.initialize();
    });
    expect(get.mock.calls.length).toBe(calls);
  });

  it('loads the profile on the next call when the first call had no user id', async () => {
    tokenUserId = null;
    const { result } = renderHook(() => useInitializeUserProfile());
    await act(async () => {
      await result.current.initialize();
    });
    expect(useUserStore.getState().profile).toBeNull();

    tokenUserId = 'u1';
    answer({ role: 'admin' }, [{ type: 'everyone' }]);
    await act(async () => {
      await result.current.initialize();
    });
    expect(useUserStore.getState().profile?.isAdmin).toBe(true);
  });

  it('still trusts an admin group when the user record fails', async () => {
    answer(new Error('500'), [{ type: 'admin' }]);
    expect(await resolvedIsAdmin()).toBe(true);
  });

  it('treats a user record without a role and no admin group as a member', async () => {
    answer({ fullName: 'A' }, [{ type: 'everyone' }]);
    expect(await resolvedIsAdmin()).toBe(false);
  });
});
