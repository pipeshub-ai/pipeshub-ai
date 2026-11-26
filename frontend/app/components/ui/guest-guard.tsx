'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/config';
import { peekReturnTo } from '@/lib/auth/post-sign-in-return';
import { getSafeReturnTo } from '@/lib/utils/safe-return-to';
import { LoadingScreen } from './auth-guard';

/**
 * Where a signed-in visitor goes: the address `returnTo`, else what the tab remembered, else the chat. The same as
 * the sign-in's own destination: this guard runs as the sign-in completes, so it must not send it elsewhere (WP-54).
 */
function signedInDestination(): string {
  const fromAddress = getSafeReturnTo(new URLSearchParams(window.location.search).get('returnTo'));
  return fromAddress ?? peekReturnTo() ?? '/chat';
}

/**
 * Blocks rendering of public/guest routes (e.g. login) until auth state is resolved.
 *
 * - While the Zustand store is rehydrating from localStorage: shows a loading screen.
 * - Once hydrated and already authenticated: redirects to `returnTo` (address or remembered), else /chat
 *   (loading screen prevents flash of login form).
 * - Once hydrated and not authenticated: renders children.
 */
export function GuestGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isHydrated = useAuthStore((s) => s.isHydrated);

  useEffect(() => {
    if (!isHydrated) return;
    if (isAuthenticated) {
      router.replace(signedInDestination());
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
