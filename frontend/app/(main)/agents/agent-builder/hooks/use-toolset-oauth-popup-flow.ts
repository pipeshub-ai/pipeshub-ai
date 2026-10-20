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
import { isWebUrl } from '@/app/(main)/workspace/mcp-servers/hooks/use-mcp-oauth-popup';
import { ToolsetsApi } from '@/app/(main)/toolsets/api';
import {
  DesktopOAuthError,
  desktopIntegrationErrorMessage,
  isElectron,
  runDesktopIntegrationOAuth,
} from '@/lib/electron';

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
 *
 * Call `beginOAuth` straight from the click handler: the popup opens (blank) before the sign-in
 * address is fetched, because browsers with strict popup rules block a window opened after an
 * async gap.
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
  /** Desktop only: cancels the wait for the browser to hand the callback back. */
  const desktopWaitStopRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    authenticatingRef.current = authenticating;
  }, [authenticating]);

  const clearOAuthPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    desktopWaitStopRef.current?.();
    desktopWaitStopRef.current = null;
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
        oauthPopupRef.current = null;
        setAuthenticating(false);
        onOAuthPopupError?.(msg);
      }
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [authenticating, clearOAuthPoll, completeOAuthFlowIfNeeded, onOAuthPopupError, t]);

  const beginOAuth = useCallback(
    async (
      windowName: string,
      fetchAuthorizationUrl: () => Promise<string>,
      handlers: ToolsetOauthBeginHandlers
    ) => {
      oauthCompletionHandledRef.current = false;
      oauthVerifyAbortRef.current = false;
      setAuthenticating(true);
      if (isElectron()) {
        try {
          const authorizationUrl = await fetchAuthorizationUrl();
          if (!isWebUrl(authorizationUrl)) {
            throw new Error(t('agentBuilder.oauthUrlFailed'));
          }
          clearOAuthPoll();
          const flow = runDesktopIntegrationOAuth({
            family: 'toolset',
            authUrl: authorizationUrl,
            complete: async ({ code, state }) => {
              const data = await ToolsetsApi.completeToolsetOAuthCallback({
                code,
                state,
                oauthError: null,
                baseUrl: '',
              });
              if (data.success === false || !(data.success || data.redirectUrl || data.redirect_url)) {
                throw new Error(data.errorMessage?.trim() || t('agentBuilder.toolsetOAuthBackendRejected'));
              }
            },
          });
          desktopWaitStopRef.current = flow.cancel;
          flow.promise.then(
            () => {
              desktopWaitStopRef.current = null;
              void completeOAuthFlowIfNeeded();
            },
            (error: unknown) => {
              desktopWaitStopRef.current = null;
              const message = desktopIntegrationErrorMessage(error);
              if (message === null || !aliveRef.current) return;
              oauthCompletionHandledRef.current = false;
              setAuthenticating(false);
              const reason = error instanceof DesktopOAuthError ? error.reason : null;
              if (reason === 'timeout') handlers.onTimeout();
              else if (reason === 'open-failed' || reason === 'unavailable') handlers.onOpenError(new Error(message));
              else if (onOAuthPopupError) onOAuthPopupError(message);
              else onIncomplete();
            }
          );
        } catch (e) {
          clearOAuthPoll();
          setAuthenticating(false);
          handlers.onOpenError(e);
        }
        return;
      }
      const popup = openCenteredOAuthWindow('about:blank', windowName);
      try {
        if (!popup || popup.closed) {
          throw new Error(t('agentBuilder.oauthPopupBlocked'));
        }
        oauthPopupRef.current = popup;
        const authorizationUrl = await fetchAuthorizationUrl();
        if (!isWebUrl(authorizationUrl)) {
          throw new Error(t('agentBuilder.oauthUrlFailed'));
        }
        if (popup.closed) {
          // Closed while the address was on its way: the person changed their mind.
          oauthPopupRef.current = null;
          setAuthenticating(false);
          return;
        }
        popup.location.href = authorizationUrl;
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
        if (popup && !popup.closed) popup.close();
        setAuthenticating(false);
        handlers.onOpenError(e);
      }
    },
    [clearOAuthPoll, completeOAuthFlowIfNeeded, onIncomplete, onOAuthPopupError, t]
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
