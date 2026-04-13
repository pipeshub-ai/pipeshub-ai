'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { TFunction } from 'i18next';
import {
  OAUTH_POPUP_MAX_POLLS,
  OAUTH_POPUP_POLL_MS,
  OAUTH_VERIFY_ATTEMPTS,
  OAUTH_VERIFY_GAP_MS,
  openCenteredOAuthWindow,
} from '../components/toolset-oauth-constants';

export interface UseToolsetOauthPopupFlowOptions {
  t: TFunction;
  verifyAuthenticated: () => Promise<boolean>;
  onVerified: () => void;
  onNotify?: (message: string) => void;
  onIncomplete: () => void;
}

export interface ToolsetOauthBeginHandlers {
  onTimeout: () => void;
  onOpenError: (error: unknown) => void;
}

/**
 * Popup OAuth for toolsets: open window, poll until it closes, verify with backoff, listen for `oauth-success` postMessage.
 */
export function useToolsetOauthPopupFlow({
  t,
  verifyAuthenticated,
  onVerified,
  onNotify,
  onIncomplete,
}: UseToolsetOauthPopupFlowOptions) {
  const [authenticating, setAuthenticating] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const oauthPopupRef = useRef<Window | null>(null);
  const oauthCompletionHandledRef = useRef(false);
  const oauthVerifyAbortRef = useRef(false);
  const authenticatingRef = useRef(false);
  const aliveRef = useRef(true);

  useEffect(() => {
    authenticatingRef.current = authenticating;
  }, [authenticating]);

  const clearOAuthPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(
    () => () => {
      aliveRef.current = false;
      oauthVerifyAbortRef.current = true;
      clearOAuthPoll();
    },
    [clearOAuthPoll]
  );

  const stopOAuthUi = useCallback(() => {
    oauthVerifyAbortRef.current = true;
    oauthCompletionHandledRef.current = false;
    clearOAuthPoll();
    const pop = oauthPopupRef.current;
    oauthPopupRef.current = null;
    if (pop && !pop.closed) {
      try {
        pop.close();
      } catch {
        /* ignore */
      }
    }
    setAuthenticating(false);
  }, [clearOAuthPoll]);

  const completeOAuthFlowIfNeeded = useCallback(async () => {
    if (oauthCompletionHandledRef.current) return;
    oauthCompletionHandledRef.current = true;
    clearOAuthPoll();
    const pop = oauthPopupRef.current;
    if (pop && !pop.closed) {
      try {
        pop.close();
      } catch {
        /* ignore */
      }
    }
    oauthPopupRef.current = null;

    for (let attempt = 0; attempt < OAUTH_VERIFY_ATTEMPTS; attempt += 1) {
      if (oauthVerifyAbortRef.current || !aliveRef.current) {
        oauthCompletionHandledRef.current = false;
        setAuthenticating(false);
        return;
      }
      await new Promise((r) => setTimeout(r, OAUTH_VERIFY_GAP_MS));
      if (oauthVerifyAbortRef.current || !aliveRef.current) {
        oauthCompletionHandledRef.current = false;
        setAuthenticating(false);
        return;
      }
      try {
        const ok = await verifyAuthenticated();
        if (ok) {
          if (oauthVerifyAbortRef.current || !aliveRef.current) {
            oauthCompletionHandledRef.current = false;
            setAuthenticating(false);
            return;
          }
          setAuthenticating(false);
          onNotify?.(t('agentBuilder.oauthSignInSuccess'));
          onVerified();
          return;
        }
      } catch {
        /* retry */
      }
    }
    if (!aliveRef.current || oauthVerifyAbortRef.current) {
      oauthCompletionHandledRef.current = false;
      setAuthenticating(false);
      return;
    }
    setAuthenticating(false);
    oauthCompletionHandledRef.current = false;
    onIncomplete();
  }, [clearOAuthPoll, onIncomplete, onNotify, onVerified, t, verifyAuthenticated]);

  useEffect(() => {
    if (!authenticating) return;
    const onMsg = (event: MessageEvent) => {
      if (event.data?.type !== 'oauth-success') return;
      const pop = oauthPopupRef.current;
      if (!pop) return;
      if (event.source && event.source !== pop) return;
      void completeOAuthFlowIfNeeded();
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [authenticating, completeOAuthFlowIfNeeded]);

  const beginOAuth = useCallback(
    async (
      fetchAuthorization: () => Promise<{ authorizationUrl: string; windowName: string }>,
      handlers: ToolsetOauthBeginHandlers
    ) => {
      oauthCompletionHandledRef.current = false;
      oauthVerifyAbortRef.current = false;
      setAuthenticating(true);
      try {
        const { authorizationUrl, windowName } = await fetchAuthorization();
        const popup = openCenteredOAuthWindow(authorizationUrl, windowName);
        if (!popup) {
          throw new Error(t('agentBuilder.oauthPopupBlocked'));
        }
        oauthPopupRef.current = popup;
        popup.focus();

        let statusChecked = false;
        let pollCount = 0;
        clearOAuthPoll();
        pollRef.current = setInterval(() => {
          pollCount += 1;
          if (pollCount >= OAUTH_POPUP_MAX_POLLS) {
            clearOAuthPoll();
            if (!popup.closed) {
              try {
                popup.close();
              } catch {
                /* ignore */
              }
            }
            oauthPopupRef.current = null;
            oauthCompletionHandledRef.current = false;
            setAuthenticating(false);
            handlers.onTimeout();
            return;
          }
          if (!popup.closed || statusChecked || oauthCompletionHandledRef.current) return;
          statusChecked = true;
          void completeOAuthFlowIfNeeded();
        }, OAUTH_POPUP_POLL_MS);
      } catch (e) {
        clearOAuthPoll();
        oauthPopupRef.current = null;
        setAuthenticating(false);
        handlers.onOpenError(e);
      }
    },
    [clearOAuthPoll, completeOAuthFlowIfNeeded, t]
  );

  const cancelForUserDismissal = useCallback(() => {
    stopOAuthUi();
    onNotify?.(t('agentBuilder.oauthSignInCancelled'));
  }, [onNotify, stopOAuthUi, t]);

  return {
    authenticating,
    authenticatingRef,
    beginOAuth,
    stopOAuthUi,
    cancelForUserDismissal,
  };
}
