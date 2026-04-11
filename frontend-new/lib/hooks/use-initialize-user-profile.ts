'use client';

/**
 * useInitializeUserProfile
 *
 * Orchestrates the four-source profile resolution and pushes results
 * into the UserStore via synchronous setters.
 *
 * Sources:
 *   1. JWT decode    → userId
 *   2. GET /api/v1/users/:userId         → firstName, lastName, fullName, email, hasLoggedIn
 *   3. GET /api/v1/userGroups/users/:id  → isAdmin (group.type === 'admin')
 *   4. GET /api/v1/users/:userId/logo    → avatarUrl (blob, silent fail)
 *
 * Idempotent: skips if already initialized unless `force = true`.
 * Called by <UserProfileInitializer> in the (main) layout.
 */

import { useCallback, useRef } from 'react';
import { useAuthStore } from '@/lib/store/auth-store';
import { useUserStore } from '@/lib/store/user-store';
import { apiClient } from '@/lib/api';
import { getUserIdFromToken } from '@/lib/utils/jwt';

const LOG = '[user-initializer]';

export function useInitializeUserProfile() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const { setProfile, setLoading, setError, setInitialized, clearProfile } = useUserStore();

  // Track the previous userId to detect re-login in the same tab
  const prevUserIdRef = useRef<string | null>(null);

  const initialize = useCallback(
    async (force = false) => {
      // Always read current state from the store — never use closed-over values,
      // which may be stale when multiple concurrent calls race through this guard.
      const { isLoading, isInitialized } = useUserStore.getState();

      if (!force && isLoading) {
        console.debug(LOG, 'Skipping — initialization already in-flight');
        return;
      }
      if (!force && isInitialized) {
        console.debug(LOG, 'Skipping — profile already initialized', useUserStore.getState().profile);
        return;
      }

      console.debug(LOG, 'Starting profile initialization', { force });
      setLoading(true);
      setError(null);

      try {
        const userId = getUserIdFromToken();
        console.debug(LOG, 'Decoded userId from JWT:', userId);

        if (!userId) {
          console.warn(LOG, 'No userId in token — aborting');
          setLoading(false);
          setInitialized(true);
          return;
        }

        // Capture userId at the start of this async chain to detect staleness
        const capturedUserId = userId;

        // ── Parallel: profile + admin groups ──────────────────
        console.debug(LOG, 'Fetching user profile + groups in parallel…');
        const [userResult, groupsResult] = await Promise.allSettled([
          apiClient.get(`/api/v1/users/${userId}`),
          apiClient.get(`/api/v1/userGroups/users/${userId}`),
        ]);

        const user =
          userResult.status === 'fulfilled'
            ? userResult.value.data
            : null;

        if (userResult.status === 'rejected') {
          console.warn(LOG, 'User API failed:', userResult.reason);
        } else {
          console.debug(LOG, 'User API OK:', {
            name: user?.fullName,
            email: user?.email,
          });
        }

        const groups =
          groupsResult.status === 'fulfilled'
            ? (groupsResult.value.data as Array<{ type: string }>)
            : null;

        if (groupsResult.status === 'rejected') {
          console.warn(LOG, 'Groups API failed:', groupsResult.reason);
        } else {
          console.debug(LOG, 'Groups API OK, count:', Array.isArray(groups) ? groups.length : 0);
        }

        const isAdmin = Array.isArray(groups)
          ? groups.some((g) => g.type === 'admin')
          : null;

        console.debug(LOG, 'isAdmin resolved:', isAdmin);

        // ── Staleness check ───────────────────────────────────
        // If the user logged out or re-logged in mid-flight, abort to avoid
        // clobbering a newer profile or leaking the fresh blob URL.
        if (getUserIdFromToken() !== capturedUserId) {
          console.debug(LOG, 'Stale initialization — userId changed mid-flight, aborting');
          return;
        }

        const email = user?.email ?? null;
        const fullName =
          user?.fullName ??
          ([user?.firstName, user?.lastName].filter(Boolean).join(' ') || null) ??
          (email ? email.split('@')[0] : null);

        const resolvedProfile = {
          userId,
          firstName: user?.firstName ?? null,
          lastName: user?.lastName ?? null,
          fullName,
          email,
          isAdmin,
          hasLoggedIn: user?.hasLoggedIn ?? false,
        };

        setProfile({ ...resolvedProfile, avatarUrl: null });
        setInitialized(true);
        setLoading(false);
        if (process.env.NODE_ENV !== 'production') {
          console.debug(LOG, 'Profile initialized successfully — full resolved profile:', resolvedProfile);
          console.debug(LOG, 'UserStore state after resolution:', useUserStore.getState());
        }
      } catch (error) {
        console.error(LOG, 'Unexpected error during initialization:', error);
        setError('Failed to load user profile');
        setLoading(false);
        setInitialized(true);
      }
    },
    // Setters are stable Zustand actions — no deps that change, keeping initialize
    // stable so the UserProfileInitializer effect doesn't re-fire on store updates.
    []
  );

  return {
    isHydrated,
    isAuthenticated,
    accessToken,
    prevUserIdRef,
    initialize,
    clearProfile,
  };
}
