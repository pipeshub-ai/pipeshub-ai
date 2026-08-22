'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
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

interface UseMcpOAuthPopupOptions {
  /** Re-checks the instance's `isAuthenticated` flag; return true once verified. */
  verifyAuthenticated: () => Promise<boolean>;
  /** Called once verification succeeds — refresh lists / close dialogs, etc. */
  onVerified?: () => void;
  /**
   * Called when OAuth does not complete successfully (popup blocked/closed without
   * auth, error postMessage, verify timeout, missing authorize URL, or fetch error).
   * Use to clear page-level busy state that `onVerified` would otherwise leave stuck.
   */
  onFailed?: () => void;
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
 * Mirrors `useConnectorOAuthPopup` / toolset's popup flow: open the authorization URL
 * in a popup, listen for `postMessage` from the callback page, verify with retries
 * (gives the backend time to persist tokens), then report back to the caller.
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

  const completeOAuthFlow = useCallback(async () => {
    if (completionHandledRef.current) return;
    completionHandledRef.current = true;
    clearPoll();

    const pop = popupRef.current;
    popupRef.current = null;
    if (pop && !pop.closed) {
      try {
        pop.close();
      } catch {
        /* ignore */
      }
    }

    if (!aliveRef.current) return;

    let verified = false;
    for (let attempt = 0; attempt < OAUTH_VERIFY_ATTEMPTS; attempt++) {
      if (verifyAbortRef.current || !aliveRef.current) break;
      await new Promise<void>((r) => setTimeout(r, OAUTH_VERIFY_GAP_MS));
      if (verifyAbortRef.current || !aliveRef.current) break;
      try {
        verified = await verifyAuthenticatedRef.current();
        if (verified) break;
      } catch {
        /* retry */
      }
    }

    if (verifyAbortRef.current || !aliveRef.current) return;

    if (verified) {
      setStatus('success');
      onVerifiedRef.current?.();
    } else {
      completionHandledRef.current = false;
      setStatus('failed');
      onFailedRef.current?.();
    }
  }, [clearPoll]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const type = (event.data as { type?: string } | null)?.type;

      if (isMcpOAuthSuccessMessageType(type)) {
        void completeOAuthFlow();
        return;
      }
      if (isMcpOAuthErrorMessageType(type)) {
        verifyAbortRef.current = true;
        completionHandledRef.current = false;
        clearPoll();
        popupRef.current = null;
        setStatus('failed');
        onFailedRef.current?.();
      }
    };

    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [completeOAuthFlow, clearPoll]);

  const startOAuthPopup = useCallback(
    async (instanceId: string) => {
      clearPoll();
      completionHandledRef.current = false;
      verifyAbortRef.current = false;
      popupRef.current = null;
      setStatus('authenticating');

      try {
        const fetchAuthUrl =
          getAuthorizationUrlRef.current ??
          ((id: string) => McpServersApi.getOAuthAuthorizationUrl(id, window.location.origin));
        const { authorizationUrl } = await fetchAuthUrl(instanceId);
        if (!authorizationUrl) {
          setStatus('failed');
          onFailedRef.current?.();
          return;
        }

        const popup = openCenteredOAuthWindow(authorizationUrl, `mcp-oauth-${instanceId}`);
        if (!popup || popup.closed) {
          setStatus('idle');
          onFailedRef.current?.();
          return;
        }

        popupRef.current = popup;
        popup.focus();

        let pollCount = 0;
        pollRef.current = setInterval(() => {
          pollCount += 1;
          if (pollCount >= OAUTH_POPUP_MAX_POLLS) {
            clearPoll();
            completionHandledRef.current = false;
            setStatus('idle');
            onFailedRef.current?.();
            return;
          }
          if (!popup.closed || completionHandledRef.current) return;
          void completeOAuthFlow();
        }, OAUTH_POPUP_POLL_MS);
      } catch {
        clearPoll();
        popupRef.current = null;
        setStatus('failed');
        onFailedRef.current?.();
      }
    },
    [clearPoll, completeOAuthFlow]
  );

  const reset = useCallback(() => setStatus('idle'), []);

  return { startOAuthPopup, status, reset };
}
