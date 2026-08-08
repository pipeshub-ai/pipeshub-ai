'use client';

import { useCallback } from 'react';
import { useAuthStore } from '@/config';
import { useOrgStore } from '@/lib/store/org-store';

export function useInitializeOrgProfile() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const clearOrg = useOrgStore((s) => s.clearOrg);

  const initialize = useCallback((force = false) => {
    if (!force && useOrgStore.getState().isInitialized) return;
    useOrgStore.getState().setInitialized(true);
  }, []);

  return { isHydrated, isAuthenticated, accessToken, initialize, clearOrg };
}
