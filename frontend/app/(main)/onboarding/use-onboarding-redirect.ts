'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/config';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { useOnboardingStore } from './store';
import { getOnboardingStatus } from './api';

/**
 * Sends an admin to /onboarding while the org still needs setting up.
 * Only admins can save the onboarding steps, so members (and users whose role
 * is not known yet) stay in the app instead of landing on a page they cannot finish.
 */
export function useOnboardingRedirect(): void {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const isAdmin = useUserStore(selectIsAdmin);
  const setOnboardingActive = useOnboardingStore((s) => s.setOnboardingActive);

  useEffect(() => {
    // Already on the onboarding page: bail out to prevent a redirect loop
    if (window.location.pathname.startsWith('/onboarding')) return;
    if (!isHydrated || !isAuthenticated || isAdmin !== true) return;

    getOnboardingStatus()
      .then(({ status }) => {
        if (status === 'notConfigured') {
          setOnboardingActive(true);
          router.replace('/onboarding');
        }
      })
      .catch(() => {
        // If the check fails (network, permissions) proceed normally — don't block the app
      });
  }, [isHydrated, isAuthenticated, isAdmin, router, setOnboardingActive]);
}
