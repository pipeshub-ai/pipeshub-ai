import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

// The store persists to localStorage; stand in for just the selector surface the hook reads.
let userState: { profile: { isAdmin: boolean | null } | null } = { profile: null };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof userState) => unknown) => selector(userState),
  selectIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
}));

import { useEnterprisePrompt } from '../use-enterprise-prompt';

function shown(isAdmin: boolean | null): boolean {
  userState = { profile: isAdmin === null ? null : { isAdmin } };
  return renderHook(() => useEnterprisePrompt()).result.current;
}

describe('useEnterprisePrompt', () => {
  it('always shows Enterprise placeholders to admins', () => {
    expect(shown(true)).toBe(true);
  });

  it('never shows them to someone who is not an admin, or before the profile loads', () => {
    expect(shown(false)).toBe(false);
    expect(shown(null)).toBe(false);
  });
});
