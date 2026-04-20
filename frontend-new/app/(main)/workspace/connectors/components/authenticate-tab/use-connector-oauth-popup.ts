'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useConnectorsStore } from '../../store';
import { CONNECTOR_OAUTH_POST_MESSAGE } from '@/app/(main)/connectors/oauth/connector-oauth-window-messages';
import { isConnectorConfigAuthenticated } from '../../utils/auth-helpers';

// ── Constants — match toolset OAuth timing exactly ───────────────────
/** Poll interval for detecting popup closure (ms). */
const OAUTH_POPUP_POLL_MS = 1000;
/** Maximum poll count before giving up (300 × 1 s = 5 min cap). */
const OAUTH_POPUP_MAX_POLLS = 300;
/** How many times to call the backend to confirm auth after the popup posts back. */
const OAUTH_VERIFY_ATTEMPTS = 5;
/** Gap between each verification attempt (ms). Also serves as the initial settle delay. */
const OAUTH_VERIFY_GAP_MS = 1500;
const OAUTH_POPUP_WIDTH = 600;
const OAUTH_POPUP_HEIGHT = 700;

/**
 * Open a centred OAuth popup — intentionally WITHOUT `noreferrer` / `noopener` so
 * `window.opener` stays set inside the popup, which is required for
 * `postConnectorOAuthSuccessToOpener` to reach the panel.
 */
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

// ========================================
// Types
// ========================================

export type ConnectorOAuthPopupOptions = {
  /** Bump the workspace drawer body nonce after OAuth (parity with toolset). */
  onDrawerBodyRefresh?: () => void;
  /**
   * Silent belt-and-suspenders re-fetch run after successful OAuth verification via
   * `queueMicrotask` — does NOT show the full-panel loading spinner.
   */
  onAfterConnectorOAuthHydrate?: () => void | Promise<void>;
};

// ========================================
// Hook
// ========================================

/**
 * Popup OAuth consent + postMessage callback handling for an existing connector instance.
 *
 * Architecture mirrors `useToolsetOauthPopupFlow`:
 *  - `completeOAuthFlow` is the single shared handler for both the postMessage path
 *    and the popup-close polling path (guarded by `oauthCompletionHandledRef`).
 *  - Verification uses `OAUTH_VERIFY_ATTEMPTS × OAUTH_VERIFY_GAP_MS` retries, identical
 *    to the toolset, so the backend has time to persist tokens before we check.
 *  - `isAuthenticating` stays `true` until verification + store update complete.
 *  - `aliveRef` / `oauthVerifyAbortRef` prevent stale callbacks after unmount.
 *
 * Mounted at {@link ConnectorPanel} — NOT inside the Authorize tab — so the `message`
 * listener stays alive while the panel is open (Radix Tabs unmounts inactive tab content).
 */
export function useConnectorOAuthPopup(options?: ConnectorOAuthPopupOptions) {
  const isPanelOpen = useConnectorsStore((s) => s.isPanelOpen);
  const setAuthState = useConnectorsStore((s) => s.setAuthState);

  const [isAuthenticating, setIsAuthenticating] = useState(false);

  // Keep option callbacks in refs so completeOAuthFlow never goes stale
  const onDrawerBodyRefreshRef = useRef(options?.onDrawerBodyRefresh);
  onDrawerBodyRefreshRef.current = options?.onDrawerBodyRefresh;
  const onAfterConnectorOAuthHydrateRef = useRef(options?.onAfterConnectorOAuthHydrate);
  onAfterConnectorOAuthHydrateRef.current = options?.onAfterConnectorOAuthHydrate;

  const oauthPopupRef = useRef<Window | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  /** Prevents duplicate `completeOAuthFlow` from postMessage + popup-close (toolset pattern). */
  const oauthCompletionHandledRef = useRef(false);
  /** Abort pending verification when the flow is cancelled or the panel unmounts. */
  const oauthVerifyAbortRef = useRef(false);
  /** Guards against callbacks firing after the component unmounts (toolset pattern). */
  const aliveRef = useRef(true);

  // Lifecycle — reset aliveRef on mount; abort + clean up on unmount
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      oauthVerifyAbortRef.current = true;
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, []);

  const clearOAuthPoll = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // ── Single auth check ───────────────────────────────────────────────

  /**
   * Fetch schema + config once and return `true` when the connector is authenticated.
   * On success, commits the new schema + config to the store immediately so the panel
   * re-renders with the correct state without waiting for the full flow to finish.
   */
  const checkConnectorAuth = useCallback(async (connectorId: string): Promise<boolean> => {
    const storeSnap = useConnectorsStore.getState();
    const connectorType = storeSnap.panelConnector?.type;
    if (!connectorType) return false;
    try {
      const { ConnectorsApi } = await import('../../api');
      const [schemaRes, configRes] = await Promise.all([
        ConnectorsApi.getConnectorSchema(connectorType),
        ConnectorsApi.getConnectorConfig(connectorId),
      ]);
      if (isConnectorConfigAuthenticated(configRes)) {
        const s = useConnectorsStore.getState();
        s.setSchemaAndConfig(schemaRes.schema, configRes);
        s.syncConnectorInstanceAuthFlags(connectorId, true);
        s.bumpCatalogRefresh();
        // Stay on the Authorize tab so the user sees "Connected" before moving to Configure.
        if (s.panelConnectorId === connectorId) {
          s.setPanelActiveTab('authorize');
        }
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }, []);

  // ── Shared completion handler (toolset `completeOAuthFlowIfNeeded` equivalent) ──

  /**
   * Called from both the `CONNECTOR_OAUTH_SUCCESS` postMessage path and the popup-close
   * polling path. Guards against duplicate execution via `oauthCompletionHandledRef`.
   *
   * Verifies auth with up to {@link OAUTH_VERIFY_ATTEMPTS} retries spaced
   * {@link OAUTH_VERIFY_GAP_MS} apart, then bumps the Authorize-tab key and drawer
   * nonce so the panel re-renders with the confirmed server state.
   */
  const completeOAuthFlow = useCallback(async () => {
    if (oauthCompletionHandledRef.current) return;
    oauthCompletionHandledRef.current = true;
    clearOAuthPoll();

    // Close popup if still open
    const pop = oauthPopupRef.current;
    oauthPopupRef.current = null;
    if (pop && !pop.closed) {
      try {
        pop.close();
      } catch {
        /* ignore */
      }
    }

    const connectorId = useConnectorsStore.getState().panelConnectorId;
    if (!connectorId || !aliveRef.current) {
      oauthCompletionHandledRef.current = false;
      setIsAuthenticating(false);
      return;
    }

    // Verify with retries (same timing as toolset)
    let verified = false;
    for (let attempt = 0; attempt < OAUTH_VERIFY_ATTEMPTS; attempt++) {
      if (oauthVerifyAbortRef.current || !aliveRef.current) break;
      // Wait before each attempt — gives the backend time to persist tokens
      // (first attempt also waits, same as toolset `completeOAuthFlowIfNeeded`).
      await new Promise<void>((r) => setTimeout(r, OAUTH_VERIFY_GAP_MS));
      if (oauthVerifyAbortRef.current || !aliveRef.current) break;
      try {
        verified = await checkConnectorAuth(connectorId);
        if (verified) break;
      } catch {
        /* retry */
      }
    }

    if (oauthVerifyAbortRef.current || !aliveRef.current) {
      oauthCompletionHandledRef.current = false;
      setIsAuthenticating(false);
      return;
    }

    if (verified) {
      // Bump UI signals so the Authorize tab remounts showing "Connected".
      useConnectorsStore.getState().bumpOAuthAuthorizeUiEpoch();
      onDrawerBodyRefreshRef.current?.();
      setIsAuthenticating(false);
      // Belt-and-suspenders silent re-fetch via microtask (mirrors toolset's queueMicrotask
      // for onVerified — avoids triggering the full-panel loader).
      queueMicrotask(() => {
        if (oauthVerifyAbortRef.current || !aliveRef.current) return;
        void onAfterConnectorOAuthHydrateRef.current?.();
      });
    } else {
      // All attempts exhausted — reset to allow retry.
      // Explicitly clear 'authenticating' authState so the button doesn't stay in
      // loading state after setIsAuthenticating(false).
      oauthCompletionHandledRef.current = false;
      useConnectorsStore.getState().setAuthState('empty');
      useConnectorsStore.getState().bumpOAuthAuthorizeUiEpoch();
      onDrawerBodyRefreshRef.current?.();
      setIsAuthenticating(false);
    }
  }, [clearOAuthPoll, checkConnectorAuth]);

  // ── postMessage listener ────────────────────────────────────────────

  useEffect(() => {
    if (!isPanelOpen) return;

    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const type = (event.data as { type?: string } | null)?.type;

      if (type === CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS) {
        // Trigger the shared completion handler (same as popup-close path).
        void completeOAuthFlow();
        return;
      }

      if (type === CONNECTOR_OAUTH_POST_MESSAGE.ERROR) {
        // Provider rejected or user cancelled — abort any in-flight verification.
        oauthVerifyAbortRef.current = true;
        oauthCompletionHandledRef.current = false;
        clearOAuthPoll();
        const p = oauthPopupRef.current;
        oauthPopupRef.current = null;
        if (p && !p.closed) {
          try {
            p.close();
          } catch {
            /* ignore */
          }
        }
        setIsAuthenticating(false);
        setAuthState('failed');
      }
    };

    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [isPanelOpen, completeOAuthFlow, clearOAuthPoll, setAuthState]);

  // ── Main entry point ────────────────────────────────────────────────

  const startOAuthPopup = useCallback(async () => {
    const id = useConnectorsStore.getState().panelConnectorId;
    if (!id) return;

    // Reset all flow state before a new attempt
    clearOAuthPoll();
    oauthCompletionHandledRef.current = false;
    oauthVerifyAbortRef.current = false;
    oauthPopupRef.current = null;

    setAuthState('authenticating');
    setIsAuthenticating(true);

    try {
      const { ConnectorsApi } = await import('../../api');
      const { authorizationUrl } = await ConnectorsApi.getOAuthAuthorizationUrl(id);

      const popup = openCenteredOAuthWindow(authorizationUrl, `connector-oauth-${id}`);
      if (!popup || popup.closed) {
        // Popup was blocked
        setIsAuthenticating(false);
        const cfg = useConnectorsStore.getState().connectorConfig;
        setAuthState(isConnectorConfigAuthenticated(cfg) ? 'success' : 'empty');
        return;
      }

      oauthPopupRef.current = popup;
      popup.focus();

      // Poll for popup closure — same guard as toolset (`statusChecked` / `oauthCompletionHandledRef`)
      let pollCount = 0;
      pollRef.current = setInterval(() => {
        pollCount += 1;
        if (pollCount >= OAUTH_POPUP_MAX_POLLS) {
          // Timeout — give up; reset authState so the button does not stay stuck in loading.
          clearOAuthPoll();
          oauthCompletionHandledRef.current = false;
          useConnectorsStore.getState().setAuthState('empty');
          setIsAuthenticating(false);
          return;
        }
        // `oauthCompletionHandledRef` prevents duplicate if postMessage already fired
        if (!popup.closed || oauthCompletionHandledRef.current) return;
        void completeOAuthFlow();
      }, OAUTH_POPUP_POLL_MS);
    } catch {
      clearOAuthPoll();
      oauthPopupRef.current = null;
      setIsAuthenticating(false);
      setAuthState('failed');
    }
  }, [setAuthState, clearOAuthPoll, completeOAuthFlow]);

  return { startOAuthPopup, isAuthenticating };
}
