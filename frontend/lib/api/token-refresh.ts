/**
 * Shared token-refresh primitives.
 *
 * Single source of truth for:
 * - JWT payload decoding
 * - Expiry check (with configurable safety buffer)
 * - The refresh network call + in-memory lock
 *
 * Both the axios interceptors (lib/api/axios-instance.ts) and the proactive
 * timer (lib/api/token-refresh-scheduler.ts) and the streaming API (lib/api/streaming.ts)
 * call into this module so that all concurrent refresh attempts collapse onto a single in-flight network
 * request via a shared `refreshPromise`.
 */

import { useAuthStore, logoutAndRedirect } from '@/config';
import { onSessionEnd, captureSession } from '@/lib/api/session-cache';
import {
  REFRESH_TOKEN_ENDPOINT,
  accountOfToken,
  requestAccessToken,
  signedInMark,
} from '@/lib/auth/session-credential';
import { nextRetryDelayMs, REFRESH_MAX_TOTAL_WAIT_MS, setSessionReconnecting } from '@/lib/api/refresh-retry';

/** Endpoint that issues a new access token from the session's refresh token. */
export { REFRESH_TOKEN_ENDPOINT };

/**
 * Default expiry buffer (seconds). A token expiring within this window is
 * treated as expired so a refresh fires before any request is denied.
 */
export const DEFAULT_EXPIRY_BUFFER_SECONDS = 90;

interface JwtPayload {
  exp?: number;
  [key: string]: unknown;
}

/** Decode a JWT payload without verifying the signature. Returns `null` on any error. */
export function decodeJwtPayload(token: string | null): JwtPayload | null {
  try {
    if (!token) return null;
    const parts = token.split('.');
    if (parts.length < 2) return null;
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const payload = typeof atob === 'function' ? atob(base64) : '';
    if (!payload) return null;
    return JSON.parse(payload) as JwtPayload;
  } catch {
    return null;
  }
}

/** Returns the `exp` claim (seconds since epoch) or `null` if missing/invalid. */
export function getTokenExpirySeconds(token: string | null): number | null {
  const decoded = decodeJwtPayload(token);
  if (!decoded || typeof decoded.exp !== 'number') return null;
  return decoded.exp;
}

/** True when the token's `exp` falls within `bufferSeconds` of now (or is past). */
export function isTokenExpired(
  token: string | null,
  bufferSeconds: number = DEFAULT_EXPIRY_BUFFER_SECONDS,
): boolean {
  const exp = getTokenExpirySeconds(token);
  if (exp === null) return false;
  const nowSeconds = Date.now() / 1000;
  return exp < nowSeconds + bufferSeconds;
}

/**
 * What one refresh found: a new token, a session the server refused, no answer, or a token for
 * another account than this page's (someone else signed in to this browser meanwhile).
 */
export type RefreshAnswer = 'refreshed' | 'refused' | 'unreachable' | 'account-changed';

/**
 * Both tokens name an account and the accounts differ. Unknown on either side is not a change.
 * Callers run it on the very token they are about to send, read in the same step.
 */
export function accountChanged(before: string | null | undefined, after: string | null | undefined): boolean {
  const a = accountOfToken(before);
  const b = accountOfToken(after);
  return a !== null && b !== null && a !== b;
}

// ── Shared in-memory refresh lock ────────────────────────────────────────
// `refreshPromise` is the single in-flight refresh; concurrent callers
// (timer + axios interceptors) all await the same promise, so the network
// call to /refresh/token is never duplicated within a tab.
let refreshPromise: Promise<RefreshAnswer> | null = null;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** True while a refresh network call is in flight. Used by the axios response interceptor. */
export function isRefreshInProgress(): boolean {
  return refreshPromise !== null;
}

/**
 * Get a new access token from the session's refresh token (the web app's
 * HttpOnly cookie, the desktop's keychain; ADR-WEB-02).
 *
 * Returns `'refreshed'` when the auth store holds a fresh access token;
 * `'refused'` when the server refused the session (or it ended while the
 * refresh was out); `'unreachable'` when the server gave no verdict for the
 * whole retry cycle (network failure, 5xx, 408, 429, or a throw before it
 * answered), so the session may still be good. Callers own the failure path
 * (`refreshOrSignOut`).
 *
 * An answer that is known to be temporary is retried with capped, jittered
 * backoff that honours `Retry-After`, for at most `REFRESH_MAX_TOTAL_WAIT_MS`;
 * then the cycle ends `'unreachable'` and the "Reconnecting…" notice shows. Any
 * final answer ends it at once.
 *
 * Concurrent calls collapse onto the same in-flight promise.
 */
export async function refreshAccessToken(): Promise<RefreshAnswer> {
  if (refreshPromise) {
    return refreshPromise;
  }

  const current = captureSession();
  const mark = signedInMark();
  // Signed out while the refresh was out, here or in another tab this one has not heard
  // from yet: storing the token would sign the last user back in.
  const over = () => !current() || signedInMark() !== mark;
  const tokenBefore = useAuthStore.getState().accessToken;
  const promise = (async (): Promise<RefreshAnswer> => {
    const startedAt = Date.now();
    try {
      // shortcut: each tab keeps its own backoff (the session lock only serialises attempts), so N open tabs
      // make up to N attempts per window during an outage; share the backoff across tabs if that load matters.
      for (let attempt = 0; ; attempt += 1) {
        const answer = await requestAccessToken();
        if (over()) return 'refused';
        if (answer.outcome === 'issued') {
          // Another account's token: storing it would hand this page, its caches and its open requests to
          // them. The page reloads instead, as when another tab signs in as someone else.
          if (accountChanged(tokenBefore, answer.accessToken)) {
            setSessionReconnecting(false);
            window.location.assign('/chat/');
            return 'account-changed';
          }
          useAuthStore.getState().setAccessToken(answer.accessToken);
          setSessionReconnecting(false);
          return 'refreshed';
        }
        if (answer.outcome === 'refused') {
          console.log('Token refresh failed:', answer.outcome);
          setSessionReconnecting(false);
          return 'refused';
        }
        const wait = nextRetryDelayMs(attempt, answer.retryAfterMs);
        if (Date.now() - startedAt + wait > REFRESH_MAX_TOTAL_WAIT_MS) {
          console.log('Token refresh failed:', answer.outcome);
          setSessionReconnecting(true);
          return 'unreachable';
        }
        await sleep(wait);
        if (over()) return 'refused';
      }
    } catch (error) {
      console.error('Error refreshing token:', error);
      // The server never answered (for example the cross-tab lock was aborted): no verdict.
      return 'unreachable';
    }
  })();

  refreshPromise = promise;
  try {
    return await promise;
  } finally {
    if (refreshPromise === promise) refreshPromise = null;
  }
}

// The next person must not wait on, or be answered by, the last person's refresh.
onSessionEnd(() => {
  refreshPromise = null;
});

/**
 * The one verdict every caller acts on. `refreshed`: the store's token may be used for the caller's
 * request. `account-changed`: it may not (it is another account's, or the request was made as another
 * account); the caller drops the request without sending it.
 */
export type RefreshOutcome = 'refreshed' | 'unreachable' | 'signed-out' | 'session-ended' | 'account-changed';

/** The refresh ended this session (or it had ended already): the request must be dropped. */
export const sessionOver = (outcome: RefreshOutcome): boolean =>
  outcome === 'signed-out' || outcome === 'session-ended';

/**
 * Refreshes the access token. On a refusal it signs out, unless the session that
 * asked has already ended: then signing out again would sign out the next person.
 * With no answer for the whole retry cycle (`'unreachable'`) it keeps the session:
 * the caller fails only what needed the token, and the "Reconnecting…" notice shows.
 * `madeWith` is the token the caller's request was made with, when it has one.
 */
export async function refreshOrSignOut(madeWith?: string | null): Promise<RefreshOutcome> {
  const current = captureSession();
  const answer = await refreshAccessToken();
  if (answer === 'account-changed') return 'account-changed';
  if (answer === 'refreshed') {
    return accountChanged(madeWith, useAuthStore.getState().accessToken) ? 'account-changed' : 'refreshed';
  }
  if (!current()) return 'session-ended';
  // No answer is no verdict: the session may still be good, so it is kept.
  if (answer === 'unreachable') return 'unreachable';
  logoutAndRedirect();
  return 'signed-out';
}
