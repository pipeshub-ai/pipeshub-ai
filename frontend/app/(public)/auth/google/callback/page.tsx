'use client';

import { useEffect, useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { buildDesktopDeepLink, isDesktopOAuthState } from '@/lib/auth/desktop-oauth';
import DesktopHandoffNotice from '@/app/(public)/auth/desktop-handoff-notice';

/** One handoff per page load: React strict mode runs the effect twice. */
let handedOff = false;

/**
 * Google OAuth2 implicit flow callback.
 *
 * Google redirects here after the user selects an account:
 *   /auth/google/callback#id_token=eyJ...&state=...
 *
 * We validate the `state` param (CSRF), then post the id_token back to the
 * opener window and close the popup. The GoogleSignInButton component listens
 * for this message and forwards the id_token to the backend.
 *
 * Under Electron this page runs in the user's own browser instead, and hands
 * the id_token to the app over a pipeshub:// deep link. See lib/auth/desktop-oauth.ts.
 */
export default function GoogleCallbackPage() {
  const [handoffLink, setHandoffLink] = useState<string | null>(null);

  useEffect(() => {
    // The id_token is in the fragment (hash), not query params —
    // Google's implicit flow never sends it to the server.
    const hash = window.location.hash.substring(1); // strip leading '#'
    const params = new URLSearchParams(hash);
    const idToken = params.get('id_token');
    const error = params.get('error');
    const errorDescription = params.get('error_description');
    const state = params.get('state');

    // A desktop sign-in ran in this browser rather than in a popup the app
    // owns, so there is no opener to answer and no matching localStorage to
    // check against. Hand the result over and let the app validate its own
    // state. Runs before the localStorage reads below so a desktop handoff
    // never clears a web flow in progress in this same browser profile.
    if (isDesktopOAuthState(state)) {
      if (handedOff) return;
      handedOff = true;
      const deepLink = buildDesktopDeepLink('google', {
        state,
        id_token: idToken,
        error,
        error_description: errorDescription,
      });
      setHandoffLink(deepLink);
      window.location.href = deepLink;
      return;
    }

    // Validate state (CSRF protection) — GoogleSignInButton stores the expected
    // value in localStorage before opening the popup.
    const expectedState = localStorage.getItem('google_oauth_state');
    localStorage.removeItem('google_oauth_state');
    localStorage.removeItem('google_oauth_nonce');

    if (expectedState && state !== expectedState) {
      if (window.opener) {
        window.opener.postMessage(
          {
            type: 'GOOGLE_AUTH_ERROR',
            error: 'Authentication response validation failed. Please try again.',
          },
          window.location.origin,
        );
      }
      window.close();
      return;
    }

    if (idToken && window.opener) {
      window.opener.postMessage(
        { type: 'GOOGLE_AUTH_SUCCESS', idToken },
        window.location.origin,
      );
    } else if (window.opener) {
      window.opener.postMessage(
        {
          type: 'GOOGLE_AUTH_ERROR',
          error: errorDescription || error || 'Google sign-in failed.',
        },
        window.location.origin,
      );
    }

    window.close();
  }, []);

  if (handoffLink) return <DesktopHandoffNotice deepLink={handoffLink} />;

  return (
    <Flex align="center" justify="center" style={{ height: '100vh' }}>
      <Text size="2" color="gray">Completing sign-in…</Text>
    </Flex>
  );
}
