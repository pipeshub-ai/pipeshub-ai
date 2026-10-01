'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import {
  buildDesktopRedirectUri,
  desktopOAuthErrorMessage,
  isElectron,
  runDesktopOAuth,
} from '@/lib/electron';
import { makeDesktopState } from '@/lib/auth/desktop-oauth';
import { AuthApi } from '../../api';
import ProviderButton from './provider-button';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface OAuthSignInButtonProps {
  /** OAuth provider display name (e.g. "Google", "Okta"). */
  providerName: string;
  /** OAuth client ID from org auth config. */
  clientId: string;
  /** IdP authorization endpoint URL. */
  authorizationUrl: string;
  /** OAuth scopes. Defaults to "openid email profile". */
  scope?: string;
  /** Redirect URI registered at the IdP. Defaults to {origin}/auth/oauth/callback. */
  redirectUri?: string;
  /** Called with the provider access token on success. */
  onSuccess: (accessToken: string) => void;
  /** Called when the OAuth flow fails. */
  onError: (message: string) => void;
  /** Render as accent-filled primary button. */
  primary?: boolean;
  /** Show loading state on the button. */
  loading?: boolean;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * OAuthSignInButton — opens a popup to a generic OAuth provider's authorization
 * endpoint with response_type=code. The callback page at /auth/oauth/callback
 * exchanges the code with the backend, then posts the access token back via
 * postMessage.
 */
export default function OAuthSignInButton({
  providerName,
  clientId,
  authorizationUrl,
  scope = 'openid email profile',
  redirectUri,
  onSuccess,
  onError,
  primary,
  loading,
}: OAuthSignInButtonProps) {
  const [isLoading, setIsLoading] = useState(false);
  const cleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      cleanupRef.current?.();
    };
  }, []);

  const handleLogin = useCallback(() => {
    cleanupRef.current?.();

    if (isElectron()) {
      // The IdP cannot redirect to app://, so the flow runs in the user's
      // browser and the code comes back by deep link; this app redeems it.
      let resolvedRedirectUri: string;
      try {
        resolvedRedirectUri = redirectUri || buildDesktopRedirectUri('oauth');
      } catch (err) {
        const message = desktopOAuthErrorMessage(err, providerName);
        if (message) onError(message);
        return;
      }
      const random = Array.from(window.crypto.getRandomValues(new Uint8Array(16)))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');
      const state = makeDesktopState(random);
      const params = new URLSearchParams({
        response_type: 'code',
        client_id: clientId,
        scope,
        redirect_uri: resolvedRedirectUri,
        state,
      });
      const flow = runDesktopOAuth({
        provider: 'oauth',
        authUrl: `${authorizationUrl}?${params}`,
        expectedState: state,
      });
      cleanupRef.current = () => {
        flow.cancel();
        cleanupRef.current = null;
      };
      setIsLoading(true);
      flow.promise
        .then(async (result) => {
          if (!result.code) throw new Error('No authorization code received.');
          const tokens = await AuthApi.exchangeOAuthCode({
            code: result.code,
            provider: 'oauth',
            redirectUri: resolvedRedirectUri,
          });
          if (!tokens.accessToken) {
            throw new Error('Authentication succeeded but no access token was returned.');
          }
          cleanupRef.current = null;
          setIsLoading(false);
          onSuccess(tokens.accessToken);
        })
        .catch((err: unknown) => {
          cleanupRef.current = null;
          setIsLoading(false);
          const message = desktopOAuthErrorMessage(err, providerName);
          if (message) onError(message);
        });
      return;
    }

    const resolvedRedirectUri =
      redirectUri || `${window.location.origin}/auth/oauth/callback`;

    const statePayload = JSON.stringify({ provider: providerName });
    const state = btoa(statePayload);

    localStorage.setItem('oauth_state', state);

    const params = new URLSearchParams({
      response_type: 'code',
      client_id: clientId,
      scope,
      redirect_uri: resolvedRedirectUri,
      state,
    });

    const url = `${authorizationUrl}?${params}`;
    const width = 500;
    const height = 600;
    const left = Math.round((window.screen.width - width) / 2);
    const top = Math.round((window.screen.height - height) / 2);

    const popup = window.open(
      url,
      'oauth-signin',
      `width=${width},height=${height},left=${left},top=${top},resizable=no,scrollbars=yes,status=no`,
    );

    if (!popup) {
      localStorage.removeItem('oauth_state');
      onError('Sign-in popup was blocked. Please allow popups for this site.');
      return;
    }

    setIsLoading(true);

    const cleanup = () => {
      window.removeEventListener('message', handleMessage);
      clearInterval(checkClosed);
      localStorage.removeItem('oauth_state');
      cleanupRef.current = null;
    };

    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;

      if (event.data?.type === 'OAUTH_SUCCESS') {
        cleanup();
        setIsLoading(false);
        onSuccess(event.data.accessToken);
      } else if (event.data?.type === 'OAUTH_ERROR') {
        cleanup();
        setIsLoading(false);
        onError(event.data.error || 'OAuth sign-in failed. Please try again.');
      }
    };

    window.addEventListener('message', handleMessage);

    const checkClosed = setInterval(() => {
      try {
        if (popup.closed) {
          cleanup();
          setIsLoading(false);
        }
      } catch {
        // COOP policy may block window.closed access; rely on postMessage
      }
    }, 500);

    cleanupRef.current = cleanup;
  }, [clientId, authorizationUrl, scope, redirectUri, providerName, onSuccess, onError]);

  return (
    <ProviderButton
      provider="oauth"
      oauthProviderName={providerName}
      onClick={handleLogin}
      loading={loading || isLoading}
      primary={primary}
    />
  );
}
