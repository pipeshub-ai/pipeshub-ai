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

import {
  useAuthStore,
  REFRESH_TOKEN_STORAGE_KEY,
} from '@/lib/store/auth-store';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';

/** Endpoint that issues a new access token from a valid refresh token. */
export const REFRESH_TOKEN_ENDPOINT = '/api/v1/userAccount/refresh/token';

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
 * Refresh the access token using the stored refresh token.
 *
 * Returns `true` when the auth store has been updated with a fresh access
 * token, `false` on any failure (no refresh token, network error, non-2xx
 * response, or response missing `accessToken`/`token`). Callers are
 * responsible for handling the failure path (typically `logoutAndRedirect`).
 *
 * Concurrent calls collapse onto the same in-flight promise.
 */
export async function refreshAccessToken(): Promise<boolean> {
  if (refreshPromise) {
    return refreshPromise;
  }

  refreshPromise = (async () => {
    try {
      const refreshToken =
        useAuthStore.getState().refreshToken ??
        (typeof window !== 'undefined'
          ? window.localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY)
          : null);

      if (!refreshToken) {
        console.log('No refresh token available');
        return false;
      }

      // Bypass axios so the request interceptor doesn't recurse into refresh.
      const response = await fetch(`${getApiBaseUrl()}${REFRESH_TOKEN_ENDPOINT}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${refreshToken}`,
        },
      });

      if (!response.ok) {
        console.log('Token refresh request failed:', response.status);
        return false;
      }

      const data = await response.json();
      const newAccessToken: string | undefined = data.accessToken;

      if (newAccessToken) {
        useAuthStore.getState().setTokens(newAccessToken, refreshToken);
        return true;
      }

      return false;
    } catch (error) {
      console.error('Error refreshing token:', error);
      return false;
    }
  })();

  try {
    return await refreshPromise;
  } finally {
    refreshPromise = null;
  }
}
