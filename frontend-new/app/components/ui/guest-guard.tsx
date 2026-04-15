'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/lib/store/auth-store';
import { LoadingScreen } from './auth-guard';

/**
 * Blocks rendering of public/guest routes (e.g. login) until auth state is resolved.
 *
 * - While the Zustand store is rehydrating from localStorage: shows a loading screen.
 * - Once hydrated and already authenticated: redirects to /chat (loading screen prevents flash of login form).
 * - Once hydrated and not authenticated: renders children.
 */
export function GuestGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isHydrated = useAuthStore((s) => s.isHydrated);

  useEffect(() => {
    if (!isHydrated) return;
    if (isAuthenticated) {
      router.replace('/chat');
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
