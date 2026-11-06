import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useUserStore, type UserProfile } from '@/lib/store/user-store';

const initialize = vi.fn();
vi.mock('@/lib/hooks/use-initialize-user-profile', () => ({
  useInitializeUserProfile: () => ({
    isHydrated: true,
    isAuthenticated: true,
    accessToken: 'token',
    prevUserIdRef: { current: 'u1' },
    initialize,
    clearProfile: vi.fn(),
  }),
}));
vi.mock('@/lib/utils/jwt', () => ({ getUserIdFromToken: () => 'u1' }));

import { ADMIN_ROLE_RETRY_MS, UserProfileInitializer } from '../user-profile-initializer';

const profile = { userId: 'u1', isAdmin: null } as UserProfile;

describe('UserProfileInitializer admin-role retry', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    initialize.mockReset();
    useUserStore.getState().clearProfile();
  });

  it('retries a refresh that left the admin role unknown', () => {
    useUserStore.setState({ profile, isInitialized: true, isLoading: false, error: null });
    render(<UserProfileInitializer />);
    initialize.mockClear();

    act(() => vi.advanceTimersByTime(ADMIN_ROLE_RETRY_MS));

    expect(initialize).toHaveBeenCalledWith(true);
  });

  it('does not retry once the role is known', () => {
    useUserStore.setState({ profile: { ...profile, isAdmin: false }, isInitialized: true, isLoading: false, error: null });
    render(<UserProfileInitializer />);
    initialize.mockClear();

    act(() => vi.advanceTimersByTime(ADMIN_ROLE_RETRY_MS * 3));

    expect(initialize).not.toHaveBeenCalled();
  });
});
