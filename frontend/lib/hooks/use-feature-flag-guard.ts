'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import {
  useFeatureFlagsStore,
  selectFeatureFlagsLoaded,
  type FeatureFlagsStore,
} from '@/lib/store/feature-flags-store';

/**
 * Redirects away from a flag-gated page once flags have loaded and the flag
 * is off — never redirects while flags are still `null` (would bounce every
 * page on first load before the fetch resolves). Extracts the pattern
 * duplicated across the MCP/Actions personal pages.
 */
export function useFeatureFlagGuard(
  selector: (s: FeatureFlagsStore) => boolean,
  redirectTo = '/workspace/general',
) {
  const enabled = useFeatureFlagsStore(selector);
  const loaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  const router = useRouter();

  useEffect(() => {
    if (loaded && !enabled) {
      router.replace(redirectTo);
    }
  }, [loaded, enabled, redirectTo, router]);

  return { enabled, loaded, ready: loaded && enabled };
}
