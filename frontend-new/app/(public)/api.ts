import { publicAuthClient } from '@/lib/api/public-auth-client';
import type { AuthMethod, SignInResponse } from '@/lib/api/auth-public-types';
import {
  getAuthSessionRequestConfig,
  postUserAccountAuthenticate,
} from '@/lib/api/post-user-account-authenticate';
import { UserAccountLoginApi } from '@/app/(main)/workspace/authentication/api';

export type { AuthMethod, SignInResponse } from '@/lib/api/auth-public-types';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface AuthInitResponse {
  currentStep: number;
  allowedMethods: AuthMethod[];
  message: string;
  authProviders: Record<string, Record<string, string>>;
  isJitProvisioning?: boolean;
  jitEnabledMethods?: string[];
}

export interface ResetPasswordResponse {
  data: string;
  accessToken?: string;
}

export interface OAuthCodeExchangePayload {
  code: string;
  provider: 'oauth' | 'google' | 'microsoft' | 'azureAd';
  redirectUri: string;
}

// ─── Auth API ─────────────────────────────────────────────────────────────────

export interface SignUpResponse {
  accessToken: string;
  refreshToken: string;
  user?: {
    id: string;
    name?: string;
    email?: string;
    phone?: string;
    created_at?: string;
    updated_at?: string;
  };
}

export const AuthApi = {
  /**
   * Register a new user account.
   * Returns an accessToken on success.
   */
  async signUp(payload: {
    email: string;
    password: string;
    firstName: string;
    lastName: string;
    registeredName: string;
  }): Promise<SignUpResponse> {
    const { data } = await publicAuthClient.post<SignUpResponse>(
      '/api/v1/org',
      {
        contactEmail: payload.email,
        registeredName: payload.registeredName,
        adminFullName: `${payload.firstName} ${payload.lastName}`.trim(),
        password: payload.password,
        accountType: 'business',
      },
    );
    return data;
  },

  /**
   * Step 1 – initialise the auth flow for an email address.
   * Returns the allowed sign-in methods. The server responds with an
   * `x-session-token` header which we read here and persist in sessionStorage
   * so subsequent requests (e.g. signInWithPassword) can attach it. Note: this
   * token lives in sessionStorage (not an httpOnly cookie) and is therefore
   * readable by JavaScript. It is a short-lived correlation token, not a
   * long-lived credential.
   */
  async initAuth(): Promise<AuthInitResponse> {
    const response = await publicAuthClient.post<AuthInitResponse>('/api/v1/userAccount/initAuth');

    // Persist the session token so subsequent requests carry it
    const sessionToken = response.headers['x-session-token'];
    if (sessionToken && typeof window !== 'undefined') {
      sessionStorage.setItem('auth_session_token', sessionToken);
    }

    return response.data;
  },

  /**
   * Request a one-time login code sent to the user's email.
   * Implementation lives in workspace authentication API (shared public client).
   */
  async generateLoginOtp(email: string): Promise<void> {
    return UserAccountLoginApi.generateLoginOtp(email);
  },

  /**
   * Complete sign-in with email OTP (6-digit code).
   */
  async signInWithOtp(email: string, otp: string): Promise<SignInResponse> {
    return UserAccountLoginApi.signInWithOtp(email, otp);
  },

  /**
   * Step 2 – authenticate with email + password.
   * On success the server returns { accessToken, refreshToken }.
   */
  async signInWithPassword(
    email: string,
    password: string,
  ): Promise<SignInResponse> {
    return postUserAccountAuthenticate(
      { email, method: 'password', credentials: { password } },
      getAuthSessionRequestConfig(),
    );
  },

  /**
   * Trigger a "forgot password" email.
   * The link in the email carries a short-lived JWT hash fragment `#token=...`.
   */
  async forgotPassword(email: string): Promise<{ data: string }> {
    const { data } = await publicAuthClient.post<{ data: string }>(
      '/api/v1/userAccount/password/forgot',
      { email },
    );
    return data;
  },

  /**
   * Reset password using the JWT token from the email link.
   * The token is sent as the Authorization Bearer – the server issues a new
   * accessToken on success.
   */
  async resetPasswordViaEmailLink(
    token: string,
    newPassword: string,
  ): Promise<ResetPasswordResponse> {
    const { data } = await publicAuthClient.post<ResetPasswordResponse>(
      '/api/v1/userAccount/password/reset/token',
      { password: newPassword },
      { headers: { Authorization: `Bearer ${token}` } },
    );
    return data;
  },

  /**
   * Exchange a long-lived refreshToken for a new accessToken.
   */
  async refreshAccessToken(refreshToken: string): Promise<{ accessToken: string }> {
    const { data } = await publicAuthClient.post<{ accessToken: string }>(
      '/api/v1/userAccount/token/refresh',
      { refreshToken },
    );
    return data;
  },

  /**
   * Authenticate using a Google ID token / credential obtained client-side
   * (e.g. via @react-oauth/google). Sends `credentials` + `method: 'google'`
   * to the backend which validates the token and returns app tokens.
   */
  async signInWithGoogle(credential: string): Promise<SignInResponse> {
    return postUserAccountAuthenticate(
      { credentials: credential, method: 'google' },
      getAuthSessionRequestConfig(),
    );
  },

  /**
   * Authenticate using Microsoft / Azure AD tokens obtained client-side
   * (e.g. via MSAL). Sends both `accessToken` and `idToken` to the backend.
   * Use `method: 'azureAd'` for Azure AD enterprise tenants.
   */
  async signInWithMicrosoft(
    credentials: { accessToken: string; idToken: string },
    method: 'microsoft' | 'azureAd' = 'microsoft',
  ): Promise<SignInResponse> {
    return postUserAccountAuthenticate(
      { credentials, method },
      getAuthSessionRequestConfig(),
    );
  },

  /**
   * Authenticate using a generic OAuth provider's access token.
   * The token is obtained after the callback page exchanges the authorization
   * code via /oauth/exchange. This method sends it to /authenticate with
   * method: 'oauth' so the backend can resolve the user and issue app JWTs.
   */
  async signInWithOAuth(accessToken: string): Promise<SignInResponse> {
    return postUserAccountAuthenticate(
      { method: 'oauth', credentials: { accessToken } },
      getAuthSessionRequestConfig(),
    );
  },

  /**
   * Exchange OAuth authorization code for application tokens.
   */
  async exchangeOAuthCode(
    payload: OAuthCodeExchangePayload,
  ): Promise<SignInResponse> {
    const sessionToken =
      typeof window !== 'undefined'
        ? sessionStorage.getItem('auth_session_token')
        : null;

    const { data } = await publicAuthClient.post<SignInResponse & {
      access_token?: string;
      refresh_token?: string;
    }>(
      '/api/v1/userAccount/oauth/exchange',
      payload,
      sessionToken
        ? { headers: { 'x-session-token': sessionToken } }
        : undefined,
    );

    return {
      ...data,
      accessToken: data.accessToken ?? data.access_token,
      refreshToken: data.refreshToken ?? data.refresh_token,
    };
  },
};

export default AuthApi;
