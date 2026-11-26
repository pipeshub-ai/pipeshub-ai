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
  requestAccessToken,
  signedInMark,
} from '@/lib/auth/session-credential';

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

// ── Shared in-memory refresh lock ────────────────────────────────────────
// `refreshPromise` is the single in-flight refresh; concurrent callers
// (timer + axios interceptors) all await the same promise, so the network
// call to /refresh/token is never duplicated within a tab.
let refreshPromise: Promise<boolean> | null = null;

/** True while a refresh network call is in flight. Used by the axios response interceptor. */
export function isRefreshInProgress(): boolean {
  return refreshPromise !== null;
}

/**
 * Get a new access token from the session's refresh token (the web app's
 * HttpOnly cookie, the desktop's keychain; ADR-WEB-02).
 *
 * Returns `true` when the auth store has been updated with a fresh access
 * token, `false` on any failure (no session, network error, refused).
 * Callers are responsible for the failure path (typically `logoutAndRedirect`).
 *
 * Concurrent calls collapse onto the same in-flight promise.
 */
export async function refreshAccessToken(): Promise<boolean> {
  if (refreshPromise) {
    return refreshPromise;
  }

  const current = captureSession();
  const mark = signedInMark();
  const promise = (async () => {
    try {
      const answer = await requestAccessToken();
      if (answer.outcome !== 'issued') {
        console.log('Token refresh failed:', answer.outcome);
        return false;
      }
      // Signed out while the refresh was out, here or in another tab this one has not heard
      // from yet: storing the token would sign the last user back in.
      if (!current() || signedInMark() !== mark) return false;
      useAuthStore.getState().setAccessToken(answer.accessToken);
      return true;
    } catch (error) {
      console.error('Error refreshing token:', error);
      return false;
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

export type RefreshOutcome = 'refreshed' | 'signed-out' | 'session-ended';

/**
 * Refreshes the access token. On failure it signs out, unless the session that
 * asked has already ended: then signing out again would sign out the next person.
 */
export async function refreshOrSignOut(): Promise<RefreshOutcome> {
  const current = captureSession();
  if (await refreshAccessToken()) return 'refreshed';
  if (!current()) return 'session-ended';
  logoutAndRedirect();
  return 'signed-out';
}
