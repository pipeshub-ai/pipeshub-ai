import type { AxiosRequestConfig } from 'axios';
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
 * POST /api/v1/userAccount/authenticate (password, OTP, Google, Microsoft, OAuth token, etc.).
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
    throw error;
  }
}
