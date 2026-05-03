'use client';

/**
 * UserProfileInitializer
 *
 * Mounts invisibly inside the (main) layout. Delegates all logic to
 * `useInitializeUserProfile`. Watches auth state and:
 *   • Triggers initialization when the user is authenticated.
 *   • Re-initializes (force) when the decoded userId changes (true re-login, not token refresh).
 *   • Clears the profile when the user is no longer authenticated.
 *
 * Renders nothing — purely a side-effect component.
 */

import { useEffect } from 'react';
import { useInitializeUserProfile } from '@/lib/hooks/use-initialize-user-profile';
import { getUserIdFromToken } from '@/lib/utils/jwt';

const LOG = '[user-initializer]';

export function UserProfileInitializer() {
  const {
    isHydrated,
    isAuthenticated,
    accessToken,
    prevUserIdRef,
    initialize,
    clearProfile,
  } = useInitializeUserProfile();

  useEffect(() => {
    // Wait for the auth store to rehydrate from localStorage
    if (!isHydrated) {
      console.debug(LOG, 'Waiting for auth store to hydrate…');
      return;
    }

    if (isAuthenticated && accessToken) {
      // Decode userId from the current token — only force re-init when the
      // authenticated user actually changes (true re-login), not on every
      // silent token refresh which only changes the token string.
      const currentUserId = getUserIdFromToken();
      const userIdChanged =
        prevUserIdRef.current !== null && prevUserIdRef.current !== currentUserId;

      if (userIdChanged) {
        console.debug(LOG, 'User changed — forcing re-initialization');
      } else {
        console.debug(LOG, 'Authenticated — initializing profile');
      }

      initialize(userIdChanged);
      prevUserIdRef.current = currentUserId;
    } else {
      if (prevUserIdRef.current !== null) {
        console.debug(LOG, 'User unauthenticated — clearing profile');
      }
      clearProfile();
      prevUserIdRef.current = null;
    }
  }, [isHydrated, isAuthenticated, accessToken, initialize, clearProfile, prevUserIdRef]);

  return null;
}
