'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import ProviderButton from './provider-button';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface MicrosoftSignInButtonProps {
  /** Azure AD / Microsoft Entra application (client) ID. */
  clientId: string;
  /** Authority URL. Defaults to the multi-tenant common endpoint. */
  authority?: string;
  /**
   * Called with Microsoft tokens on success.
   * Pass `accessToken` + `idToken` to AuthApi.signInWithMicrosoft.
   */
  onSuccess: (credentials: { accessToken: string; idToken: string }) => void;
  /** Called when the popup flow fails (excluding user-cancelled events). */
  onError: (message: string) => void;
  /** True while parent is calling the authenticate API (keeps button busy after token exchange). */
  authLoading?: boolean;
  /** Render as accent-filled primary button instead of outline. */
  primary?: boolean;
}

// ─── PKCE helpers ─────────────────────────────────────────────────────────────

function base64UrlEncode(buffer: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < buffer.length; i++) {
    binary += String.fromCharCode(buffer[i]);
  }
  return btoa(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

function generateRandomString(length = 48): string {
  const array = new Uint8Array(length);
  crypto.getRandomValues(array);
  return Array.from(array, (b) => b.toString(16).padStart(2, '0')).join('');
}

async function generatePKCE(): Promise<{ verifier: string; challenge: string }> {
  const array = new Uint8Array(32);
  crypto.getRandomValues(array);
  const verifier = base64UrlEncode(array);
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(verifier),
  );
  const challenge = base64UrlEncode(new Uint8Array(digest));
  return { verifier, challenge };
}

async function exchangeCodeForTokens(params: {
  authority: string;
  clientId: string;
  code: string;
  codeVerifier: string;
  redirectUri: string;
}): Promise<{ accessToken: string; idToken: string }> {
  const tokenEndpoint = `${params.authority.replace(/\/$/, '')}/oauth2/v2.0/token`;

  const response = await fetch(tokenEndpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      client_id: params.clientId,
      code: params.code,
      code_verifier: params.codeVerifier,
      redirect_uri: params.redirectUri,
      grant_type: 'authorization_code',
      scope: 'openid profile email',
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      (error as { error_description?: string }).error_description ||
        'Token exchange with Microsoft failed.',
    );
  }

  const data = (await response.json()) as {
    access_token?: string;
    id_token?: string;
  };

  return {
    accessToken: data.access_token || '',
    idToken: data.id_token || '',
  };
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * MicrosoftSignInButton — opens a popup to Microsoft's authorization endpoint
 * with PKCE, receives the auth code via postMessage from the callback page,
 * exchanges it client-side for tokens, and forwards them to the caller.
 *
 * After tokens are exchanged, local loading is cleared so the parent’s `authLoading`
 * (authenticate API) controls the button. If that API fails, the button re-enables.
 */
export default function MicrosoftSignInButton({
  clientId,
  authority = 'https://login.microsoftonline.com/common',
  onSuccess,
  onError,
  authLoading = false,
  primary,
}: MicrosoftSignInButtonProps) {
  const [isLoading, setIsLoading] = useState(false);
  const popupRef = useRef<Window | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const codeVerifierRef = useRef('');

  /** Stop polling and drop popup ref only (used after success while redirect is pending). */
  const disposePopupOnly = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    popupRef.current = null;
  }, []);

  /** Full reset: popup resources, PKCE state, and button loading (errors / user cancel). */
  const resetFlow = useCallback(() => {
    disposePopupOnly();
    codeVerifierRef.current = '';
    setIsLoading(false);
  }, [disposePopupOnly]);

  useEffect(() => {
    const handleMessage = async (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (!codeVerifierRef.current) return;

      if (event.data?.type === 'MICROSOFT_AUTH_SUCCESS') {
        const verifier = codeVerifierRef.current;
        if (!verifier) return;
        // Consume verifier immediately so a duplicate postMessage (e.g. Strict Mode)
        // cannot start a second token exchange for the same single-use code.
        codeVerifierRef.current = '';

        try {
          const tokens = await exchangeCodeForTokens({
            authority,
            clientId,
            code: event.data.code,
            codeVerifier: verifier,
            redirectUri: `${window.location.origin}/auth/microsoft/callback`,
          });

          if (!tokens.idToken) {
            throw new Error('No id token received from Microsoft.');
          }

          onSuccess(tokens);
          disposePopupOnly();
          setIsLoading(false);
        } catch (err) {
          resetFlow();
          onError(
            err instanceof Error ? err.message : 'Token exchange failed.',
          );
        }
      } else if (event.data?.type === 'MICROSOFT_AUTH_ERROR') {
        resetFlow();
        onError(event.data.error || 'Microsoft sign-in failed.');
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [authority, clientId, disposePopupOnly, resetFlow, onSuccess, onError]);

  const handleClick = async () => {
    if (isLoading || authLoading) return;
    setIsLoading(true);

    try {
      try {
        for (let i = sessionStorage.length - 1; i >= 0; i--) {
          const k = sessionStorage.key(i);
          if (k?.startsWith('ms_oauth_cb_')) sessionStorage.removeItem(k);
        }
      } catch {
        // ignore
      }

      const state = generateRandomString();
      const { verifier, challenge } = await generatePKCE();
      codeVerifierRef.current = verifier;
      localStorage.setItem('microsoft_oauth_state', state);

      const redirectUri = `${window.location.origin}/auth/microsoft/callback`;
      const authorizeEndpoint = `${authority.replace(/\/$/, '')}/oauth2/v2.0/authorize`;

      const params = new URLSearchParams({
        client_id: clientId,
        response_type: 'code',
        redirect_uri: redirectUri,
        scope: 'openid profile email',
        state,
        response_mode: 'fragment',
        prompt: 'select_account',
        code_challenge: challenge,
        code_challenge_method: 'S256',
      });

      const width = 500;
      const height = 700;
      const left = window.screenX + (window.outerWidth - width) / 2;
      const top = window.screenY + (window.outerHeight - height) / 2;

      const popup = window.open(
        `${authorizeEndpoint}?${params.toString()}`,
        'microsoft_auth_popup',
        `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`,
      );

      if (!popup) {
        resetFlow();
        onError(
          'Popup was blocked. Please allow popups for this site and try again.',
        );
        return;
      }

      popupRef.current = popup;

      pollingRef.current = setInterval(() => {
        if (!popupRef.current || popupRef.current.closed) {
          disposePopupOnly();
          // Popup may close before postMessage is delivered; wait before treating as abandon.
          const snapshot = codeVerifierRef.current;
          if (!snapshot) return;
          window.setTimeout(() => {
            if (codeVerifierRef.current === snapshot) {
              try {
                localStorage.removeItem('microsoft_oauth_state');
              } catch {
                // ignore
              }
              codeVerifierRef.current = '';
              setIsLoading(false);
            }
          }, 600);
        }
      }, 500);
    } catch {
      resetFlow();
      onError('Failed to start Microsoft sign-in. Please try again.');
    }
  };

  const busy = isLoading || authLoading;

  return (
    <ProviderButton
      provider="microsoft"
      onClick={handleClick}
      loading={busy}
      primary={primary}
    />
  );
}
