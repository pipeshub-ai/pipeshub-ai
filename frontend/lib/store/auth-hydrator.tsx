'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { hydrateAuthStore, LOGIN_NAVIGATION_EVENT } from './auth-store';

/**
 * Client-only component that hydrates the auth store from localStorage
 * after React has mounted. Mount once near the root of every layout
 * (public + main) so `useAuthStore().isHydrated` flips to `true`
 * and downstream gates (AuthGuard, GuestGuard, login page) can proceed.
 */
export function AuthHydrator(): null {
  const router = useRouter();

  useEffect(() => {
    hydrateAuthStore();
  }, []);

  useEffect(() => {
    const goLogin = () => {
      router.replace('/login');
    };
    window.addEventListener(LOGIN_NAVIGATION_EVENT, goLogin);
    return () => window.removeEventListener(LOGIN_NAVIGATION_EVENT, goLogin);
  }, [router]);

  return null;
}
