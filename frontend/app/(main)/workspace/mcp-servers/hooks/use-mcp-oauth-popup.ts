'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { TFunction } from 'i18next';
import { isProcessedError } from '@/lib/api';
import { McpServersApi } from '../api';
import {
  isMcpOAuthErrorMessageType,
  isMcpOAuthSuccessMessageType,
} from '../oauth/mcp-oauth-window-messages';

// ── Constants — match the connector/toolset OAuth popup timing ──────────
const OAUTH_POPUP_POLL_MS = 1000;
/** Maximum poll count before giving up (300 x 1s = 5 min cap). */
const OAUTH_POPUP_MAX_POLLS = 300;
/** How many times to re-check backend auth status after the popup posts back. */
const OAUTH_VERIFY_ATTEMPTS = 5;
const OAUTH_VERIFY_GAP_MS = 1500;
const OAUTH_POPUP_WIDTH = 600;
const OAUTH_POPUP_HEIGHT = 700;

/** Only http(s) sign-in URLs may be opened; a `javascript:` URL would run in this page's origin. */
export function isWebUrl(url: string): boolean {
  try {
    const { protocol } = new URL(url);
    return protocol === 'https:' || protocol === 'http:';
  } catch {
    return false;
  }
}

function openCenteredOAuthWindow(url: string, name: string): Window | null {
  const w = OAUTH_POPUP_WIDTH;
  const h = OAUTH_POPUP_HEIGHT;
  const left = Math.round(window.screen.width / 2 - w / 2);
  const top = Math.round(window.screen.height / 2 - h / 2);
  return window.open(
    url,
    name,
    `width=${w},height=${h},left=${left},top=${top},scrollbars=yes,resizable=yes`
  );
}

export type McpOAuthPopupStatus = 'idle' | 'authenticating' | 'success' | 'failed';

/** Why a sign-in didn't complete, so the page can say so. */
export type McpOAuthFailureReason =
  /** The browser blocked the popup. */
  | 'blocked'
  /** The user closed the popup without finishing. */
  | 'cancelled'
  /** The provider or the callback reported an error (`message` has it when known). */
  | 'provider_error'
  /** Nothing happened within the time limit. */
  | 'timeout'
  /** The sign-in address couldn't be obtained or isn't a web address (`message` may say why). */
  | 'unavailable';

export interface McpOAuthFailure {
  reason: McpOAuthFailureReason;
  message?: string;
}

/** The text to show for a failed sign-in. */
export function mcpOAuthFailureMessage(t: TFunction, failure: McpOAuthFailure): string {
  switch (failure.reason) {
    case 'blocked':
      return t('agentBuilder.oauthPopupBlocked');
    case 'cancelled':
      return t('agentBuilder.oauthSignInCancelled');
    case 'timeout':
      return t('workspace.mcpServers.toasts.oauthTimedOut');
    case 'provider_error':
      return failure.message
        ? t('workspace.mcpServers.toasts.oauthProviderError', { message: failure.message })
        : t('workspace.mcpServers.toasts.authenticateError');
    default:
      return failure.message || t('workspace.mcpServers.toasts.authenticateError');
  }
}

interface UseMcpOAuthPopupOptions {
  /** Re-checks the instance's `isAuthenticated` flag; return true once verified. */
  verifyAuthenticated: () => Promise<boolean>;
  /** Called once verification succeeds — refresh lists / close dialogs, etc. */
  onVerified?: () => void;
  /** Called when the sign-in doesn't complete, with the reason (see `mcpOAuthFailureMessage`). */
  onFailed?: (failure: McpOAuthFailure) => void;
  /**
   * Defaults to the current user's own instance OAuth URL
   * (`McpServersApi.getOAuthAuthorizationUrl`). Pass to scope the authorize call to an
   * agent instead (`McpServersApi.getAgentOAuthAuthorizationUrl`) — see
   * `agent-mcp-credentials-dialog.tsx`.
   */
  getAuthorizationUrl?: (instanceId: string) => Promise<{ authorizationUrl?: string }>;
}

/**
 * Popup OAuth consent + postMessage callback handling for an MCP server instance.
 *
 * Call `startOAuthPopup` straight from the click handler: the popup opens (blank) before
 * anything is awaited, because browsers with strict popup rules block a window opened after an
 * async gap, then navigates to the sign-in address once it arrives. Reconnecting is the same
 * call: the callback replaces the stored tokens only when the new sign-in succeeds.
 */
export function useMcpOAuthPopup({
  verifyAuthenticated,
  onVerified,
  onFailed,
  getAuthorizationUrl,
}: UseMcpOAuthPopupOptions) {
  const [status, setStatus] = useState<McpOAuthPopupStatus>('idle');

  const verifyAuthenticatedRef = useRef(verifyAuthenticated);
  verifyAuthenticatedRef.current = verifyAuthenticated;
  const onVerifiedRef = useRef(onVerified);
  onVerifiedRef.current = onVerified;
  const onFailedRef = useRef(onFailed);
  onFailedRef.current = onFailed;
  const getAuthorizationUrlRef = useRef(getAuthorizationUrl);
  getAuthorizationUrlRef.current = getAuthorizationUrl;

  const popupRef = useRef<Window | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const completionHandledRef = useRef(false);
  const verifyAbortRef = useRef(false);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      verifyAbortRef.current = true;
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, []);

  const clearPoll = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const closePopup = useCallback(() => {
    const pop = popupRef.current;
    popupRef.current = null;
    if (pop && !pop.closed) {
      try {
        pop.close();
      } catch {
        /* ignore */
      }
    }
  }, []);

  const fail = useCallback(
    (failure: McpOAuthFailure) => {
      clearPoll();
      closePopup();
      completionHandledRef.current = false;
      if (!aliveRef.current) return;
      setStatus('failed');
      onFailedRef.current?.(failure);
    },
    [clearPoll, closePopup]
  );

  const succeed = useCallback(() => {
    if (!aliveRef.current) return;
    setStatus('success');
    onVerifiedRef.current?.();
  }, []);

  /** The callback reported success: give the backend time to persist the tokens. */
  const completeOAuthFlow = useCallback(async () => {
    if (completionHandledRef.current) return;
    completionHandledRef.current = true;
    clearPoll();
    closePopup();
    if (!aliveRef.current) return;

    for (let attempt = 0; attempt < OAUTH_VERIFY_ATTEMPTS; attempt++) {
      if (verifyAbortRef.current || !aliveRef.current) return;
      await new Promise<void>((r) => setTimeout(r, OAUTH_VERIFY_GAP_MS));
      if (verifyAbortRef.current || !aliveRef.current) return;
      try {
        if (await verifyAuthenticatedRef.current()) {
          succeed();
          return;
        }
      } catch {
        /* retry */
      }
    }
    fail({ reason: 'provider_error' });
  }, [clearPoll, closePopup, fail, succeed]);

  /** The popup closed without a message: one check, then it was cancelled. */
  const handleClosedPopup = useCallback(async () => {
    if (completionHandledRef.current) return;
    completionHandledRef.current = true;
    clearPoll();
    popupRef.current = null;
    let verified = false;
    try {
      verified = await verifyAuthenticatedRef.current();
    } catch {
      verified = false;
    }
    if (verifyAbortRef.current || !aliveRef.current) return;
    if (verified) succeed();
    else fail({ reason: 'cancelled' });
  }, [clearPoll, fail, succeed]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      // Only our own popup's messages: another MCP sign-in open elsewhere on the page has its own.
      if (event.source && popupRef.current && event.source !== popupRef.current) return;
      const data = event.data as { type?: string; error?: unknown } | null;
      const type = data?.type;

      if (isMcpOAuthSuccessMessageType(type)) {
        void completeOAuthFlow();
        return;
      }
      if (isMcpOAuthErrorMessageType(type)) {
        verifyAbortRef.current = true;
        fail({ reason: 'provider_error', message: typeof data?.error === 'string' ? data.error : undefined });
      }
    };

    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [completeOAuthFlow, fail]);

  const startOAuthPopup = useCallback(
    async (instanceId: string) => {
      clearPoll();
      completionHandledRef.current = false;
      verifyAbortRef.current = false;
      setStatus('authenticating');

      // Opened before any await, while the click still counts as a user gesture.
      const popup = openCenteredOAuthWindow('about:blank', `mcp-oauth-${instanceId}`);
      if (!popup || popup.closed) {
        fail({ reason: 'blocked' });
        return;
      }
      popupRef.current = popup;

      let authorizationUrl: string | undefined;
      try {
        const fetchAuthUrl =
          getAuthorizationUrlRef.current ??
          ((id: string) => McpServersApi.getOAuthAuthorizationUrl(id, window.location.origin));
        ({ authorizationUrl } = await fetchAuthUrl(instanceId));
      } catch (error) {
        fail({ reason: 'unavailable', message: isProcessedError(error) ? error.message : undefined });
        return;
      }
      if (!authorizationUrl || !isWebUrl(authorizationUrl)) {
        fail({ reason: 'unavailable' });
        return;
      }
      if (popup.closed) {
        fail({ reason: 'cancelled' });
        return;
      }
      try {
        popup.location.href = authorizationUrl;
        popup.focus();
      } catch {
        fail({ reason: 'blocked' });
        return;
      }

      let pollCount = 0;
      pollRef.current = setInterval(() => {
        pollCount += 1;
        if (pollCount >= OAUTH_POPUP_MAX_POLLS) {
          fail({ reason: 'timeout' });
          return;
        }
        if (!popup.closed || completionHandledRef.current) return;
        void handleClosedPopup();
      }, OAUTH_POPUP_POLL_MS);
    },
    [clearPoll, fail, handleClosedPopup]
  );

  const reset = useCallback(() => setStatus('idle'), []);

  return { startOAuthPopup, status, reset };
}
