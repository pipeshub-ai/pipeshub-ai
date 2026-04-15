'use client';

import { useEffect, useRef, useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';

import { extractApiErrorMessage } from '@/lib/api/api-error';
import { getApiBaseUrl } from '@/lib/api/base-url';

async function readHttpErrorMessage(response: Response): Promise<string> {
  const status = response.status;
  const text = await response.text();
  if (!text.trim()) {
    return `Authentication failed (${status}).`;
  }
  try {
    const parsed: unknown = JSON.parse(text);
    const fromApi = extractApiErrorMessage(parsed);
    if (fromApi) return fromApi;
  } catch {
    // Body is not JSON — use plain text (e.g. reverse-proxy error page)
  }
  const trimmed = text.trim();
  if (trimmed.length > 500) {
    return `${trimmed.slice(0, 497)}…`;
  }
  return trimmed || `Authentication failed (${status}).`;
}

/**
 * OAuthCallbackPage — handles the redirect from a generic OAuth provider
 * (e.g. Okta) after the user authorises the application.
 *
 * Flow:
 *  1. Backend redirects here with ?code=...&state=... after the provider
 *     calls the backend's redirect URI.
 *  2. This page POSTs the code to /api/v1/userAccount/oauth/exchange.
 *  3. On success, sends tokens to the opener window via postMessage and
 *     closes itself.
 *  4. On failure, posts an error to the opener (if any), closes the popup, and
 *     shows an error UI only when there is no opener (direct navigation).
 *
 * The `state` param is a base64-encoded JSON object containing `{ provider }`
 * set by OAuthSignInButton when the popup was opened. CSRF protection is
 * enforced by comparing the received state against the value stored in
 * localStorage by the opener.
 */
export default function OAuthCallbackPage() {
  const [error, setError] = useState('');
  const hasExchanged = useRef(false);

  useEffect(() => {
    const handleCallback = async () => {
      // Prevent double-invocation in React Strict Mode dev double-mount
      if (hasExchanged.current) return;
      hasExchanged.current = true;

      try {
        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get('code');
        const state = urlParams.get('state');
        const oauthError = urlParams.get('error');

        if (oauthError) throw new Error(`OAuth error: ${oauthError}`);
        if (!code) throw new Error('No authorization code received.');
        if (!state) throw new Error('No state parameter received.');

        // CSRF validation: compare received state with the value the opener
        // stored in localStorage before opening the popup.
        const expectedState = localStorage.getItem('oauth_state');
        localStorage.removeItem('oauth_state');

        if (expectedState && state !== expectedState) {
          throw new Error('Authentication response validation failed. Please try again.');
        }

        let stateData: { email?: string; provider?: string };
        try {
          // Normalize base64url → base64: replace URL-safe chars and restore padding
          const base64 = state
            .replace(/-/g, '+')
            .replace(/_/g, '/')
            .replace(/ /g, '+'); // URLSearchParams decodes '+' as space
          const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
          stateData = JSON.parse(atob(padded));
        } catch {
          throw new Error('Invalid state parameter.');
        }

        const provider = stateData.provider?.trim();
        if (!provider) {
          throw new Error('Invalid OAuth state: missing provider.');
        }

        const baseUrl = getApiBaseUrl();
        if (!baseUrl) {
          throw new Error('Configuration error: API base URL is not set.');
        }

        const response = await fetch(
          `${baseUrl}/api/v1/userAccount/oauth/exchange`,
          {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              code,
              provider,
              redirectUri: `${window.location.origin}/auth/oauth/callback`,
            }),
          },
        );

        if (!response.ok) {
          throw new Error(await readHttpErrorMessage(response));
        }

        const tokens = (await response.json()) as {
          access_token?: string;
          accessToken?: string;
        };

        const accessToken = tokens.access_token ?? tokens.accessToken;

        if (!accessToken) {
          throw new Error('Authentication succeeded but no access token was returned.');
        }

        if (window.opener) {
          window.opener.postMessage(
            { type: 'OAUTH_SUCCESS', accessToken },
            window.location.origin,
          );
        }
        window.close();
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'OAuth authentication failed.';

        if (window.opener) {
          window.opener.postMessage(
            { type: 'OAUTH_ERROR', error: message },
            window.location.origin,
          );
          window.close();
          return;
        }

        setError(message);
      }
    };

    handleCallback();
  }, []);

  if (error) {
    return (
      <Flex
        align="center"
        justify="center"
        style={{ minHeight: '100vh', padding: 'var(--space-5)' }}
      >
        <Box style={{ maxWidth: 400, textAlign: 'center' }}>
          <Text color="red" size="3" weight="medium" style={{ display: 'block', marginBottom: 'var(--space-2)' }}>
            Sign-in failed
          </Text>
          <Text size="2" color="gray" style={{ display: 'block', marginBottom: 'var(--space-4)' }}>
            {error}
          </Text>
          <Text size="2" color="gray">
            You can close this window and try again.
          </Text>
        </Box>
      </Flex>
    );
  }

  return (
    <Flex align="center" justify="center" style={{ minHeight: '100vh' }}>
      <Text size="2" color="gray">
        Processing sign-in…
      </Text>
    </Flex>
  );
}
