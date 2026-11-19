import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

// The store persists to localStorage; stand in for it with the real selector's rule.
type State = { profile: { isAdmin: boolean | null } | null; isInitialized: boolean; error: string | null };
let userState: State = { profile: null, isInitialized: false, error: null };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: State) => unknown) => selector(userState),
  selectRefreshedIsAdmin: (s: State) => (s.isInitialized && !s.error ? (s.profile?.isAdmin ?? null) : null),
}));

import { useEnterprisePrompt } from '../use-enterprise-prompt';

function shown(state: Partial<State>): boolean {
  userState = { profile: null, isInitialized: true, error: null, ...state };
  return renderHook(() => useEnterprisePrompt()).result.current;
}

describe('useEnterprisePrompt', () => {
  it('shows Enterprise placeholders to an admin once this session has loaded their profile', () => {
    expect(shown({ profile: { isAdmin: true } })).toBe(true);
  });

  it('does not trust a stored admin role before the profile load finishes', () => {
    // e.g. someone who was an admin last session and has since been made a member.
    expect(shown({ profile: { isAdmin: true }, isInitialized: false })).toBe(false);
  });

  it('hides them when the profile could not be loaded', () => {
    expect(shown({ profile: { isAdmin: true }, error: 'Network error' })).toBe(false);
  });

  it('never shows them to someone who is not an admin', () => {
    expect(shown({ profile: { isAdmin: false } })).toBe(false);
    expect(shown({ profile: null })).toBe(false);
  });
});
