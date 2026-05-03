import type { AxiosError, AxiosRequestConfig } from 'axios';
import type { SignInResponse } from '@/lib/api/auth-public-types';
import { publicAuthClient } from '@/lib/api/public-auth-client';

export function getAuthSessionRequestConfig(): AxiosRequestConfig | undefined {
  const sessionToken =
    typeof window !== 'undefined'
      ? sessionStorage.getItem('auth_session_token')
      : null;
  return sessionToken
    ? { headers: { 'x-session-token': sessionToken } }
    : undefined;
}

/**
 * Call /api/v1/userAccount/initAuth and persist the returned session token.
 * Used to recover from an "Invalid session" response on /authenticate when the
 * cached session token is missing or expired (e.g. returning users whose
 * sessionStorage was cleared, or users who idled past the session TTL).
 */
async function refreshAuthSessionToken(): Promise<string | null> {
  try {
    const response = await publicAuthClient.post('/api/v1/userAccount/initAuth');
    const newToken = response.headers?.['x-session-token'] as string | undefined;
    if (newToken && typeof window !== 'undefined') {
      sessionStorage.setItem('auth_session_token', newToken);
    }
    return newToken ?? null;
  } catch {
    return null;
  }
}

/**
 * Returns true when the given error is the backend's "Invalid session" /
 * "Invalid session token" 401 thrown by authSessionMiddleware — i.e. the
 * failure is specifically caused by the session correlation token being
 * missing or expired, and not by bad credentials.
 */
function isInvalidSessionError(error: unknown): boolean {
  const err = error as AxiosError<{ message?: string; error?: { message?: string } }>;
  if (err?.response?.status !== 401) return false;
  const body = err.response?.data;
  const message = (
    body?.error?.message ??
    body?.message ??
    ''
  ).toLowerCase();
  return message.includes('invalid session');
}

/**
 * POST /api/v1/userAccount/authenticate (password, OTP, Google, Microsoft, OAuth token, etc.).
 *
 * If the request is rejected with "Invalid session" (the correlation token
 * from initAuth is missing or expired), we transparently re-run initAuth once
 * to obtain a fresh session token and retry the original request. This covers
 * the first-time / returning-user case where the login page was skipped or
 * the cached session token was cleared.
 */
export async function postUserAccountAuthenticate(
  body: Record<string, unknown>,
  requestConfig?: AxiosRequestConfig,
): Promise<SignInResponse> {
  try {
    const { data } = await publicAuthClient.post<SignInResponse>(
      '/api/v1/userAccount/authenticate',
      body,
      requestConfig,
    );
    return data;
  } catch (error) {
    if (!isInvalidSessionError(error)) throw error;

    const newToken = await refreshAuthSessionToken();
    if (!newToken) throw error;

    const retryConfig: AxiosRequestConfig = {
      ...(requestConfig ?? {}),
      headers: {
        ...(requestConfig?.headers ?? {}),
        'x-session-token': newToken,
      },
    };
    const { data } = await publicAuthClient.post<SignInResponse>(
      '/api/v1/userAccount/authenticate',
      body,
      retryConfig,
    );
    return data;
  }
}
