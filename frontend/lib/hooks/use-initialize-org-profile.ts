'use client';

import { useCallback } from 'react';
import { useAuthStore, useOrgStore } from '@/config';

export function useInitializeOrgProfile() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const clearOrg = useOrgStore((s) => s.clearOrg);

  const initialize = useCallback((force = false) => {
    if (!force && useOrgStore.getState().isInitialized) return;
    useOrgStore.getState().setInitialized(true);
  }, []);

  // One org per install here, so the active org never changes under the profile.
  const currentOrgId: string | null = null;

  return { isHydrated, isAuthenticated, accessToken, currentOrgId, initialize, clearOrg };
}
