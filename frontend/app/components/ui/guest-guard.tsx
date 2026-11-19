'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/config';
import { getSafeReturnTo } from '@/lib/utils/safe-return-to';
import { LoadingScreen } from './auth-guard';

/**
 * Blocks rendering of public/guest routes (e.g. login) until auth state is resolved.
 *
 * - While the Zustand store is rehydrating from localStorage: shows a loading screen.
 * - Once hydrated and already authenticated: redirects to the page's `returnTo` (an in-app path, such as the
 *   OAuth consent page that sent the user to log in), else to /chat. Signing in flips this same state, so the
 *   guard decides where a fresh login lands too (loading screen prevents flash of login form).
 * - Once hydrated and not authenticated: renders children.
 */
export function GuestGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isHydrated = useAuthStore((s) => s.isHydrated);

  useEffect(() => {
    if (!isHydrated) return;
    if (isAuthenticated) {
      const returnTo = getSafeReturnTo(new URLSearchParams(window.location.search).get('returnTo'));
      router.replace(returnTo ?? '/chat');
    //TODO (@abhishek): modify this block to wire up to actual server logic
    } else if (process.env.NEXT_PUBLIC_FORCE_SIGN_UP === 'true') {
      router.replace('/sign-up');
    }
  }, [isHydrated, isAuthenticated, router]);

  if (!isHydrated || isAuthenticated) {
    return <LoadingScreen />;
  }

  return <>{children}</>;
}
