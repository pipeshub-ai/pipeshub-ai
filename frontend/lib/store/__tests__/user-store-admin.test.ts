import { afterEach, describe, expect, it } from 'vitest';
import { selectRefreshedIsAdmin, useUserStore, type UserProfile } from '../user-store';

const profile = { userId: 'u1', isAdmin: true } as UserProfile;

describe('selectRefreshedIsAdmin', () => {
  afterEach(() => useUserStore.getState().clearProfile());

  it('ignores a persisted admin role until the profile refresh finishes', () => {
    useUserStore.setState({ profile, isInitialized: false, error: null });
    expect(selectRefreshedIsAdmin(useUserStore.getState())).toBeNull();
  });

  it('returns the role once the refresh has finished', () => {
    useUserStore.setState({ profile, isInitialized: true, error: null });
    expect(selectRefreshedIsAdmin(useUserStore.getState())).toBe(true);
  });

  it('ignores it when the refresh failed', () => {
    useUserStore.setState({ profile, isInitialized: true, error: 'Failed to load user profile' });
    expect(selectRefreshedIsAdmin(useUserStore.getState())).toBeNull();
  });
});
