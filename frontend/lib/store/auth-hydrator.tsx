'use client';

import { useEffect } from 'react';
import { hydrateAuthStore } from './auth-store';

/**
 * Client-only component that hydrates the auth store from localStorage
 * after React has mounted. Mount once near the root of every layout
 * (public + main) so `useAuthStore().isHydrated` flips to `true`
 * and downstream gates (AuthGuard, GuestGuard, login page) can proceed.
 */
export function AuthHydrator(): null {
  useEffect(() => {
    hydrateAuthStore();
  }, []);
  return null;
}
