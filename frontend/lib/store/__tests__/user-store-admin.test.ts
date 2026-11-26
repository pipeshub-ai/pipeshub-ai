import { afterEach, describe, expect, it } from 'vitest';
import { selectAdminRoleNeedsRetry, selectRefreshedIsAdmin, useUserStore, type UserProfile } from '../user-store';

const profile = { userId: 'u1', isAdmin: true } as UserProfile;

describe('selectRefreshedIsAdmin', () => {
  afterEach(() => useUserStore.getState().clearProfile());

  it('ignores a persisted admin role until the profile refresh finishes', () => {
    useUserStore.setState({ profile, isInitialized: false, error: null });
    expect(selectRefreshedIsAdmin(useUserStore.getState())).toBeNull();
  });

  it('returns the role once the refresh has finished', () => {
    useUserStore.setState({ profile, isInitialized: true, isLoading: false, error: null });
    expect(selectRefreshedIsAdmin(useUserStore.getState())).toBe(true);
  });

  it('ignores the stored role while a retry is running', () => {
    useUserStore.setState({ profile, isInitialized: true, isLoading: true, error: null });
    expect(selectRefreshedIsAdmin(useUserStore.getState())).toBeNull();
  });

  it('ignores it when the refresh failed', () => {
    useUserStore.setState({ profile, isInitialized: true, error: 'Failed to load user profile' });
    expect(selectRefreshedIsAdmin(useUserStore.getState())).toBeNull();
  });
});

describe('selectAdminRoleNeedsRetry', () => {
  afterEach(() => useUserStore.getState().clearProfile());
  const unknown = { ...profile, isAdmin: null } as UserProfile;

  it('asks for a retry when the refresh finished without a role', () => {
    useUserStore.setState({ profile: unknown, isInitialized: true, isLoading: false, error: null });
    expect(selectAdminRoleNeedsRetry(useUserStore.getState())).toBe(true);
  });

  it('asks for a retry when the refresh failed, even with a stored role', () => {
    useUserStore.setState({ profile, isInitialized: true, isLoading: false, error: 'Failed to load user profile' });
    expect(selectAdminRoleNeedsRetry(useUserStore.getState())).toBe(true);
  });

  it('does not while a refresh is running, before the first one, or once the role is known', () => {
    useUserStore.setState({ profile: unknown, isInitialized: true, isLoading: true, error: null });
    expect(selectAdminRoleNeedsRetry(useUserStore.getState())).toBe(false);
    useUserStore.setState({ profile: unknown, isInitialized: false, isLoading: false, error: null });
    expect(selectAdminRoleNeedsRetry(useUserStore.getState())).toBe(false);
    useUserStore.setState({ profile, isInitialized: true, isLoading: false, error: null });
    expect(selectAdminRoleNeedsRetry(useUserStore.getState())).toBe(false);
  });
});
