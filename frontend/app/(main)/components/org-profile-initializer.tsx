'use client';

import { useEffect, useRef } from 'react';
import { useInitializeOrgProfile } from '@/config';
import { getUserIdFromToken } from '@/lib/utils/jwt';

export function OrgProfileInitializer() {
  const { isHydrated, isAuthenticated, accessToken, initialize, clearOrg } =
    useInitializeOrgProfile();
  const prevUserIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isHydrated) return;

    if (isAuthenticated && accessToken) {
      const currentUserId = getUserIdFromToken();
      const userIdChanged =
        prevUserIdRef.current !== null && prevUserIdRef.current !== currentUserId;

      initialize(userIdChanged);
      prevUserIdRef.current = currentUserId;
    } else {
      clearOrg();
      prevUserIdRef.current = null;
    }
  }, [isHydrated, isAuthenticated, accessToken, initialize, clearOrg]);

  return null;
}
