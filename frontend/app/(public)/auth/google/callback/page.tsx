'use client';

import { useEffect } from 'react';
import { Flex, Text } from '@radix-ui/themes';

/**
 * Google OAuth2 implicit flow callback.
 *
 * Google redirects here after the user selects an account:
 *   /auth/google/callback#id_token=eyJ...&state=...
 *
 * We validate the `state` param (CSRF), then post the id_token back to the
 * opener window and close the popup. The GoogleSignInButton component listens
 * for this message and forwards the id_token to the backend.
 */
export default function GoogleCallbackPage() {
  useEffect(() => {
    // The id_token is in the fragment (hash), not query params —
    // Google's implicit flow never sends it to the server.
    const hash = window.location.hash.substring(1); // strip leading '#'
    const params = new URLSearchParams(hash);
    const idToken = params.get('id_token');
    const error = params.get('error');
    const errorDescription = params.get('error_description');
    const state = params.get('state');

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

  return (
    <Flex align="center" justify="center" style={{ height: '100vh' }}>
      <Text size="2" color="gray">Completing sign-in…</Text>
    </Flex>
  );
}
