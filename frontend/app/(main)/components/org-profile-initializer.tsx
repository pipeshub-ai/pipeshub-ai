'use client';

import { useEffect, useRef } from 'react';
import { useInitializeOrgProfile } from '@/config';
import { getUserIdFromToken } from '@/lib/utils/jwt';

export function OrgProfileInitializer() {
  const {
    isHydrated,
    isAuthenticated,
    accessToken,
    currentOrgId,
    initialize,
    clearOrg,
  } = useInitializeOrgProfile();
  const prevUserIdRef = useRef<string | null>(null);
  const prevOrgIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isHydrated) return;

    if (isAuthenticated && accessToken) {
      const currentUserId = getUserIdFromToken();
      const userIdChanged =
        prevUserIdRef.current !== null &&
        prevUserIdRef.current !== currentUserId;
      const orgIdChanged =
        prevOrgIdRef.current !== null && prevOrgIdRef.current !== currentOrgId;

      initialize(userIdChanged || orgIdChanged);
      prevUserIdRef.current = currentUserId;
      prevOrgIdRef.current = currentOrgId;
    } else {
      clearOrg();
      prevUserIdRef.current = null;
      prevOrgIdRef.current = null;
    }
  }, [
    isHydrated,
    isAuthenticated,
    accessToken,
    currentOrgId,
    initialize,
    clearOrg,
  ]);

  return null;
}
