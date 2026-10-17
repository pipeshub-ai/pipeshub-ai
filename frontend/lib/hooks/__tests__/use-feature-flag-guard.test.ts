import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace }),
}));

vi.mock('@/lib/api/axios-instance', () => ({
  apiClient: { get: vi.fn() },
}));

import { useFeatureFlagGuard } from '../use-feature-flag-guard';
import { useFeatureFlagsStore, selectSkillsEnabled } from '@/lib/store/feature-flags-store';

describe('useFeatureFlagGuard', () => {
  beforeEach(() => {
    replace.mockClear();
    useFeatureFlagsStore.setState({ flags: null, loading: false });
  });

  it('does not redirect while flags are still loading (flags === null)', () => {
    const { result } = renderHook(() => useFeatureFlagGuard(selectSkillsEnabled));

    // selectSkillsEnabled defaults "enabled" pre-load, but loaded is false —
    // the guard must gate on `loaded`, not just the selector value.
    expect(result.current.loaded).toBe(false);
    expect(replace).not.toHaveBeenCalled();
  });

  it('redirects to the default route once flags are loaded and the flag is off', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_SKILLS: false } });

    const { result } = renderHook(() => useFeatureFlagGuard(selectSkillsEnabled));

    expect(result.current.ready).toBe(false);
    expect(replace).toHaveBeenCalled();
    expect(replace).toHaveBeenCalledWith('/workspace/general');
  });

  it('redirects to a custom route when provided', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_SKILLS: false } });

    renderHook(() => useFeatureFlagGuard(selectSkillsEnabled, '/somewhere-else'));

    expect(replace).toHaveBeenCalledWith('/somewhere-else');
  });

  it('does not redirect once flags are loaded and the flag is on', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_SKILLS: true } });

    const { result } = renderHook(() => useFeatureFlagGuard(selectSkillsEnabled));

    expect(result.current.ready).toBe(true);
    expect(replace).not.toHaveBeenCalled();
  });
});
