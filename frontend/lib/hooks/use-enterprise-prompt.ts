'use client';

import { useFeatureFlagsStore, type FeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';

/** Shown by default, but hidden until flags load so an org that turned it off never sees a flash. */
const selectEnterprisePromptsEnabled = (s: Pick<FeatureFlagsStore, 'flags'>) =>
  s.flags !== null && s.flags.SHOW_ENTERPRISE_PROMPTS !== false;

/**
 * Whether to show an Enterprise Edition placeholder: only admins can act on one, and an org can
 * turn them all off with the `SHOW_ENTERPRISE_PROMPTS` Labs flag.
 */
export function useEnterprisePrompt(): boolean {
  const enabled = useFeatureFlagsStore(selectEnterprisePromptsEnabled);
  const isAdmin = useUserStore(selectIsAdmin);
  return enabled && isAdmin === true;
}
