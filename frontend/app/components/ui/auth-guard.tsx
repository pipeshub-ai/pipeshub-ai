'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { useAuthStore } from '@/config';
import { LottieLoader } from './lottie-loader';
import { getOrgExists } from '@/lib/api/org-exists-public';
import { getSafeReturnTo } from '@/lib/utils/safe-return-to';

/** `/login`, carrying the page the user was opening (a link from an email or a notification) so sign-in returns there. */
export function loginPathFor(current: string): string {
  const returnTo = getSafeReturnTo(current);
  return returnTo && !/^\/(chat\/?)?$/.test(returnTo) ? `/login?returnTo=${encodeURIComponent(returnTo)}` : '/login';
}

export function LoadingScreen() {
  return (
    <Flex
      align="center"
      justify="center"
      className="viewport-height"
      style={{ width: '100%', backgroundColor: 'var(--olive-1)' }}
    >
      <LottieLoader variant="loader" size={64} />
    </Flex>
  );
}

/**
 * Blocks rendering of protected routes until auth state is resolved.
 *
 * - While the Zustand store is rehydrating from localStorage: shows a loading screen.
 * - Once hydrated and not authenticated: redirects to /login (loading screen prevents any flash).
 * - Once hydrated and authenticated: renders children.
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  
  useEffect(() => {
    if (!isHydrated) return;
    if (!isAuthenticated) {
      let cancelled = false;
      void getOrgExists()
        .then((response) => {
          if (cancelled) return;
          const login = loginPathFor(`${window.location.pathname}${window.location.search}`);
          router.replace(response.exists === false ? '/sign-up' : login);
        })
        .catch(() => {
          if (cancelled) return;
          router.replace(loginPathFor(`${window.location.pathname}${window.location.search}`));
        });
      return () => {
        cancelled = true;
      };
    }
  }, [isHydrated, isAuthenticated, router]);

  if (!isHydrated || !isAuthenticated) {
    return <LoadingScreen />;
  }

  return <>{children}</>;
}
