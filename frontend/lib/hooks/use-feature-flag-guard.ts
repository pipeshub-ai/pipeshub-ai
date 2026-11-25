'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import {
  useFeatureFlagsStore,
  selectFeatureFlagsKnown,
  selectFeatureFlagsLoaded,
  type FeatureFlagsStore,
} from '@/lib/store/feature-flags-store';

/**
 * Redirects away from a flag-gated page once flags have loaded and the flag
 * is off — never redirects while flags are still `null` (would bounce every
 * page on first load before the fetch resolves). Extracts the pattern
 * duplicated across the MCP/Actions personal pages. A failed read counts as loaded (its stand-in keeps the page
 * usable) but never redirects: only an answer that says the flag is off does (UIADOPT1-FU-1).
 */
export function useFeatureFlagGuard(
  selector: (s: FeatureFlagsStore) => boolean,
  redirectTo = '/workspace/general',
) {
  const enabled = useFeatureFlagsStore(selector);
  const loaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  const known = useFeatureFlagsStore(selectFeatureFlagsKnown);
  const router = useRouter();

  useEffect(() => {
    if (known && !enabled) {
      router.replace(redirectTo);
    }
  }, [known, enabled, redirectTo, router]);

  return { enabled, loaded, ready: loaded && enabled };
}
