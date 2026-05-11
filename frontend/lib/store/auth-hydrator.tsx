'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import {
  hydrateAuthStore,
  LOGIN_NAVIGATION_EVENT,
  ELECTRON_SERVER_URL_NAVIGATION_EVENT,
} from './auth-store';
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
 *
 * Listens for `LOGIN_NAVIGATION_EVENT` from `logoutAndRedirect()` so auth
 * failures use App Router navigation (including Electron without a full reload),
 * and for `ELECTRON_SERVER_URL_NAVIGATION_EVENT` to return users to the server URL flow.
 */
export function AuthHydrator(): null {
  const router = useRouter();

  useEffect(() => {
    hydrateAuthStore();
    initTokenRefreshScheduler();
  }, []);

  useEffect(() => {
    const goLogin = () => {
      router.replace('/login');
    };
    const goElectronServerUrl = () => {
      router.replace('/chat/');
    };
    window.addEventListener(LOGIN_NAVIGATION_EVENT, goLogin);
    window.addEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, goElectronServerUrl);
    return () => {
      window.removeEventListener(LOGIN_NAVIGATION_EVENT, goLogin);
      window.removeEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, goElectronServerUrl);
    };
  }, [router]);

  return null;
}
