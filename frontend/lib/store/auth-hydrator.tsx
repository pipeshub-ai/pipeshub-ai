'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import {
  hydrateAuthStore,
  LOGIN_NAVIGATION_EVENT,
  ELECTRON_SERVER_URL_NAVIGATION_EVENT,
} from './auth-store';
import { isElectron } from '@/lib/electron';

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

  // Electron-only: soft-navigation handlers for logout / server-URL flows.
  // Web uses `window.location.href` (see logoutAndRedirect in auth-store.ts),
  // so these listeners would never fire there — register them only under
  // Electron to keep web behavior identical to main.
  useEffect(() => {
    if (!isElectron()) return;
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
