'use client';

import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';

/**
 * Whether to show an Enterprise Edition placeholder. Only admins can act on one, so everyone
 * else never sees them; until the profile loads it reads as no, so nothing flashes for members.
 */
export function useEnterprisePrompt(): boolean {
  return useUserStore(selectIsAdmin) === true;
}
