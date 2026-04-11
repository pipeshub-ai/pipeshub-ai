'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/lib/store/auth-store';
import { fetchAndSetCurrentUser } from '@/lib/auth/hydrate-user';
import { getCookie } from '@/lib/utils/cookies';
import { LoadingScreen } from '@/app/components/ui/auth-guard';

const ACCESS_COOKIE = 'accessToken';
const REFRESH_COOKIE = 'refreshToken';

/** Survives React Strict Mode remounts (useRef resets). */
let samlBridgeRan = false;

/** Same-origin relative path only; blocks protocol-relative and external URLs. */
function getSafeReturnTo(raw: string | null): string | null {
  if (!raw || typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed.startsWith('/')) return null;
  if (trimmed.startsWith('//')) return null;
  return trimmed;
}

export default function SamlSsoSuccessPage() {
  const router = useRouter();
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const setTokens = useAuthStore((s) => s.setTokens);
  const logout = useAuthStore((s) => s.logout);

  useEffect(() => {
    if (!isHydrated) return;
    if (samlBridgeRan) return;
    samlBridgeRan = true;

    const run = async () => {
      const accessToken = getCookie(ACCESS_COOKIE);
      const refreshToken = getCookie(REFRESH_COOKIE);

      if (!accessToken || !refreshToken) {
        logout();
        router.replace('/login?error=saml_sso');
        return;
      }

      setTokens(accessToken, refreshToken);

      const userOk = await fetchAndSetCurrentUser();
      if (!userOk) {
        logout();
        router.replace('/login?error=saml_sso');
        return;
      }

      const params = new URLSearchParams(window.location.search);
      const returnTo = getSafeReturnTo(params.get('returnTo'));
      router.replace(returnTo ?? '/');
    };

    void run().catch(() => {
      logout();
      router.replace('/login?error=saml_sso');
    });
  }, [isHydrated, logout, router, setTokens]);

  // Full-screen loader until rehydration finishes, bridge completes, and client navigates away.
  return <LoadingScreen />;
}
