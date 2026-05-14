'use client';

import { useEffect } from 'react';
import { hydrateAuthStore } from './auth-store';
import { initTokenRefreshScheduler } from '@/lib/api/token-refresh-scheduler';

/**
 * Client-only component that hydrates the auth store from localStorage
 * after React has mounted. Mount once near the root of every layout
 * (public + main) so `useAuthStore().isHydrated` flips to `true`
 * and downstream gates (AuthGuard, GuestGuard, login page) can proceed.
 *
 * Also boots the proactive token-refresh scheduler. The scheduler is a
 * module-level singleton and `initTokenRefreshScheduler` is idempotent,
 * so mounting `<AuthHydrator />` in both the public and main layouts is
 * safe.
 */
export function AuthHydrator(): null {
  useEffect(() => {
    hydrateAuthStore();
    initTokenRefreshScheduler();
  }, []);
  return null;
}
