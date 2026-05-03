'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import ProviderButton from './provider-button';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface GoogleSignInButtonProps {
  /** Google OAuth client ID from org auth config. */
  clientId: string;
  /**
   * Called with the id_token JWT on successful Google sign-in.
   * Pass it directly to AuthApi.signInWithGoogle(credential).
   */
  onSuccess: (idToken: string) => void;
  /** Called when the Google SDK reports a sign-in failure. */
  onError: (message: string) => void;
  /** Render as accent-filled primary button. */
  primary?: boolean;
  /** Show loading state on the button. */
  loading?: boolean;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * GoogleSignInButton — opens a direct OAuth2 popup to Google's auth endpoint.
 *
 * Uses response_type=id_token so the backend can verify it with verifyIdToken
 * (google-auth-library). Deliberately avoids the GIS google.accounts.id
 * library, which requires the JavaScript origin to be registered in Google
 * Cloud Console; a direct URL popup does not have this restriction.
 *
 * Setup: add `<origin>/auth/google/callback` to Authorized redirect URIs in
 * your Google Cloud Console OAuth client.
 */
export default function GoogleSignInButton({
  clientId,
  onSuccess,
  onError,
  primary,
  loading,
}: GoogleSignInButtonProps) {
  const [isLoading, setIsLoading] = useState(false);
  // Holds a cleanup fn to cancel in-flight popup listeners/timers on unmount
  const cleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      cleanupRef.current?.();
    };
  }, []);

  const handleLogin = useCallback(() => {
    // Cancel any previous in-flight attempt
    cleanupRef.current?.();

    const nonce = Array.from(
      window.crypto.getRandomValues(new Uint8Array(16)),
    ).map((b) => b.toString(16).padStart(2, '0')).join('');

    const state = Array.from(
      window.crypto.getRandomValues(new Uint8Array(16)),
    ).map((b) => b.toString(16).padStart(2, '0')).join('');

    // Store for CSRF/replay validation in the callback page
    localStorage.setItem('google_oauth_nonce', nonce);
    localStorage.setItem('google_oauth_state', state);

    const redirectUri = `${window.location.origin}/auth/google/callback`;
    const params = new URLSearchParams({
      client_id: clientId,
      redirect_uri: redirectUri,
      response_type: 'id_token',
      scope: 'openid email profile',
      nonce,
      state,
      prompt: 'select_account',
    });

    const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?${params}`;
    const width = 500;
    const height = 600;
    const left = Math.round((window.screen.width - width) / 2);
    const top = Math.round((window.screen.height - height) / 2);

    const popup = window.open(
      authUrl,
      'google-signin',
      `width=${width},height=${height},left=${left},top=${top},resizable=no,scrollbars=yes,status=no`,
    );

    if (!popup) {
      localStorage.removeItem('google_oauth_nonce');
      localStorage.removeItem('google_oauth_state');
      onError('Google sign-in popup was blocked. Please allow popups for this site.');
      return;
    }

    setIsLoading(true);

    const cleanup = () => {
      window.removeEventListener('message', handleMessage);
      clearInterval(checkClosed);
      localStorage.removeItem('google_oauth_nonce');
      localStorage.removeItem('google_oauth_state');
      cleanupRef.current = null;
    };

    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type === 'GOOGLE_AUTH_SUCCESS') {
        cleanup();
        setIsLoading(false);
        onSuccess(event.data.idToken);
      } else if (event.data?.type === 'GOOGLE_AUTH_ERROR') {
        cleanup();
        setIsLoading(false);
        onError(event.data.error || 'Google sign-in failed. Please try again.');
      }
    };

    window.addEventListener('message', handleMessage);

    // Clean up if the user closes the popup without completing auth.
    // Wrapped in try/catch because Google sets COOP: same-origin on their auth
    // page, which severs the opener relationship and makes popup.closed
    // inaccessible. When blocked, we silently continue and rely on the
    // postMessage from the callback page.
    const checkClosed = setInterval(() => {
      try {
        if (popup.closed) {
          cleanup();
          setIsLoading(false);
        }
      } catch {
        // COOP policy blocked window.closed access; rely on postMessage
      }
    }, 500);

    cleanupRef.current = cleanup;
  }, [clientId, onSuccess, onError]);

  return (
    <ProviderButton
      provider="google"
      onClick={handleLogin}
      loading={loading || isLoading}
      primary={primary}
    />
  );
}
