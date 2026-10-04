'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/config';
import { fetchAndSetCurrentUser } from '@/lib/auth/hydrate-user';
import { AuthApi } from '@/app/(public)/api';
import { LoadingScreen } from '@/app/components/ui/auth-guard';
import { buildDesktopDeepLink, isDesktopOAuthState } from '@/lib/auth/desktop-oauth';
import DesktopHandoffNotice from '@/app/(public)/auth/desktop-handoff-notice';
import { getSafeReturnTo } from '@/lib/utils/safe-return-to';

/** Survives React Strict Mode remounts (useRef resets). */
let samlBridgeRan = false;

export default function SamlSsoSuccessPage() {
  const router = useRouter();
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const setTokens = useAuthStore((s) => s.setTokens);
  const logout = useAuthStore((s) => s.logout);
  const [handoffLink, setHandoffLink] = useState<string | null>(null);

  // A desktop sign-in lands here in the user's browser with a handoff code.
  // Forward it to the app, which holds the PKCE verifier.
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
      const code = new URLSearchParams(window.location.hash.slice(1)).get('code');
      window.history.replaceState(null, '', window.location.pathname + window.location.search);

      if (!code) {
        logout();
        router.replace('/login?error=saml_sso');
        return;
      }

      const { accessToken, refreshToken } = await AuthApi.exchangeSamlWebCode(code);
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
