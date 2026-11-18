import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

// Both stores persist to localStorage; stand in for just the selector surface the hook reads.
let userState: { profile: { isAdmin: boolean | null } | null } = { profile: null };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof userState) => unknown) => selector(userState),
  selectIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
}));

let flagsState: { flags: Record<string, boolean> | null } = { flags: null };
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: typeof flagsState) => unknown) => selector(flagsState),
}));

import { useEnterprisePrompt } from '../use-enterprise-prompt';

function shown(isAdmin: boolean | null, flags: Record<string, boolean> | null): boolean {
  userState = { profile: isAdmin === null ? null : { isAdmin } };
  flagsState = { flags };
  return renderHook(() => useEnterprisePrompt()).result.current;
}

describe('useEnterprisePrompt', () => {
  it('shows prompts to an admin when the org has not set the flag', () => {
    expect(shown(true, {})).toBe(true);
    expect(shown(true, { SHOW_ENTERPRISE_PROMPTS: true })).toBe(true);
  });

  it('hides them once the org turns SHOW_ENTERPRISE_PROMPTS off in Labs', () => {
    expect(shown(true, { SHOW_ENTERPRISE_PROMPTS: false })).toBe(false);
  });

  it('hides them until flags load, so a turned-off org never sees a flash', () => {
    expect(shown(true, null)).toBe(false);
  });

  it('never shows them to someone who is not an admin, or before the profile loads', () => {
    expect(shown(false, {})).toBe(false);
    expect(shown(null, {})).toBe(false);
  });
});
