'use client';

import { useUserStore, selectRefreshedIsAdmin } from '@/lib/store/user-store';

/**
 * Whether to show an Enterprise Edition placeholder. Only admins can act on one, so everyone
 * else never sees them. Reads the refreshed role: the persisted one can be out of date (an admin
 * who was made a member) until this session's profile load finishes, and reads as no until then.
 */
export function useEnterprisePrompt(): boolean {
  return useUserStore(selectRefreshedIsAdmin) === true;
}
