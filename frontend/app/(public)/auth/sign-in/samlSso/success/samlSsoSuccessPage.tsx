'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/config';
import { fetchAndSetCurrentUser } from '@/lib/auth/hydrate-user';
import { getCookie } from '@/lib/utils/cookies';
import { LoadingScreen } from '@/app/components/ui/auth-guard';
import { buildDesktopDeepLink, isDesktopOAuthState } from '@/lib/auth/desktop-oauth';
import DesktopHandoffNotice from '@/app/(public)/auth/desktop-handoff-notice';

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
  const [handoffLink, setHandoffLink] = useState<string | null>(null);

  // A desktop sign-in lands here in the user's browser, with a handoff code
  // instead of cookies. Forward it to the app, which holds the PKCE verifier.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const state = params.get('state');
    if (!isDesktopOAuthState(state) || samlBridgeRan) return;
    samlBridgeRan = true;
    const deepLink = buildDesktopDeepLink('saml', {
      state,
      code: params.get('code'),
      saml_error: params.get('saml_error'),
    });
    setHandoffLink(deepLink);
    window.location.href = deepLink;
  }, []);

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

  if (handoffLink) return <DesktopHandoffNotice deepLink={handoffLink} />;

  // Full-screen loader until rehydration finishes, bridge completes, and client navigates away.
  return <LoadingScreen />;
}
