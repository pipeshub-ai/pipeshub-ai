'use client';

import { useEffect } from 'react';
import { LoadingScreen } from '@/app/components/ui/auth-guard';

const MS_OAUTH_CB_PREFIX = 'ms_oauth_cb_';

export default function MicrosoftCallbackPage() {
  useEffect(() => {
    const hashParams = new URLSearchParams(window.location.hash.substring(1));
    const queryParams = new URLSearchParams(window.location.search);
    const getParam = (key: string) => hashParams.get(key) || queryParams.get(key);

    const code = getParam('code');
    const state = getParam('state');
    const error = getParam('error');
    const errorDescription = getParam('error_description');

    const dedupeId = state || code?.slice(0, 48) || error || 'empty';
    try {
      const dedupeKey = `${MS_OAUTH_CB_PREFIX}${dedupeId}`;
      if (sessionStorage.getItem(dedupeKey) === '1') {
        window.close();
        return;
      }
      sessionStorage.setItem(dedupeKey, '1');
    } catch {
      // sessionStorage unavailable — opener still dedupes via PKCE verifier consumption
    }

    const expectedState = localStorage.getItem('microsoft_oauth_state');
    localStorage.removeItem('microsoft_oauth_state');

    if (expectedState && state !== expectedState) {
      if (window.opener) {
        window.opener.postMessage(
          {
            type: 'MICROSOFT_AUTH_ERROR',
            error: 'Authentication response validation failed. Please try again.',
          },
          window.location.origin,
        );
      }
      window.close();
      return;
    }

    if (error) {
      if (window.opener) {
        window.opener.postMessage(
          {
            type: 'MICROSOFT_AUTH_ERROR',
            error: errorDescription || error || 'Microsoft sign-in failed.',
          },
          window.location.origin,
        );
      }
      window.close();
      return;
    }

    if (code && window.opener) {
      window.opener.postMessage(
        { type: 'MICROSOFT_AUTH_SUCCESS', code },
        window.location.origin,
      );
    } else if (window.opener) {
      window.opener.postMessage(
        {
          type: 'MICROSOFT_AUTH_ERROR',
          error: 'No authorization code received from Microsoft.',
        },
        window.location.origin,
      );
    }

    window.close();
  }, []);

  return <LoadingScreen />;
}
