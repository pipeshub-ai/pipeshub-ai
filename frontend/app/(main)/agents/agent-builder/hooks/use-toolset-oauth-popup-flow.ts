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
import {
  isToolsetOAuthErrorMessageType,
  isToolsetOAuthSuccessMessageType,
} from '@/app/(main)/toolsets/oauth/toolset-oauth-window-messages';

export interface UseToolsetOauthPopupFlowOptions {
  t: TFunction;
  verifyAuthenticated: () => Promise<boolean>;
  onVerified: () => void;
  onNotify?: (message: string) => void;
  onIncomplete: () => void;
  /** Popup posts `oauth-error` when the callback page fails before verification. */
  onOAuthPopupError?: (message: string) => void;
}

export interface ToolsetOauthBeginHandlers {
  onTimeout: () => void;
  onOpenError: (error: unknown) => void;
}

/**
 * Popup OAuth for toolsets: open window, poll until it closes, verify with backoff,
 * listen for toolset OAuth popup messages (`TOOLSET_OAUTH_POST_MESSAGE` success/error payloads).
 */
export function useToolsetOauthPopupFlow({
  t,
  verifyAuthenticated,
  onVerified,
  onNotify,
  onIncomplete,
  onOAuthPopupError,
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
    () => {
      // Reset alive flag on every mount/remount (React Strict Mode fires cleanup → remount;
      // without this reset, aliveRef stays false after the first cleanup, permanently blocking
      // the queueMicrotask guard in completeOAuthFlowIfNeeded from ever calling onVerified).
      aliveRef.current = true;
      return () => {
        aliveRef.current = false;
        oauthVerifyAbortRef.current = true;
        clearOAuthPoll();
      };
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
          // Run after this tick so `onVerified` → dialog `onClose` is not stuck inside the
          // `message` / interval stack (Radix dialog + focus trap behave more reliably).
          queueMicrotask(() => {
            if (oauthVerifyAbortRef.current || !aliveRef.current) return;
            onVerified();
          });
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
  }, [clearOAuthPoll, onIncomplete, onVerified, verifyAuthenticated]);

  useEffect(() => {
    if (!authenticating) return;
    const onMsg = (event: MessageEvent) => {
      const pop = oauthPopupRef.current;
      if (!pop) return;
      if (event.source && event.source !== pop) return;
      if (typeof window !== 'undefined' && event.origin !== window.location.origin) return;

      const type = event.data?.type as string | undefined;
      if (isToolsetOAuthSuccessMessageType(type)) {
        void completeOAuthFlowIfNeeded();
        return;
      }
      if (isToolsetOAuthErrorMessageType(type)) {
        oauthVerifyAbortRef.current = true;
        oauthCompletionHandledRef.current = false;
        clearOAuthPoll();
        const msg =
          typeof event.data?.error === 'string' && event.data.error.trim()
            ? event.data.error
            : t('agentBuilder.oauthSignInIncomplete');
        const p = oauthPopupRef.current;
        oauthPopupRef.current = null;
        if (p && !p.closed) {
          try {
            p.close();
          } catch {
            /* ignore */
          }
        }
        setAuthenticating(false);
        onOAuthPopupError?.(msg);
      }
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [authenticating, clearOAuthPoll, completeOAuthFlowIfNeeded, onOAuthPopupError, t]);

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
