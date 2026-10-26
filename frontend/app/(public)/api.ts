import { publicAuthClient, ensureDesktopFrontendOrigin } from '@/lib/api/public-auth-client';
import type { AuthMethod, SignInResponse } from '@/lib/api/auth-public-types';
import {
  getAuthSessionRequestConfig,
  postUserAccountAuthenticate,
} from '@/lib/api/post-user-account-authenticate';
import { UserAccountLoginApi } from '@/app/(main)/workspace/authentication/api';
import { getSignupSource } from '@/lib/utils/signup-source';

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

// ─── Auth API ─────────────────────────────────────────────────────────────────

export const AuthApi = {
  /**
   * Create the organization and its first admin. The response carries no
   * session; sign in afterwards with the same email and password.
   */
  async signUp(payload: {
    email: string;
    password: string;
    firstName: string;
    lastName: string;
    registeredName: string;
  }): Promise<void> {
    await publicAuthClient.post(
      '/api/v1/org',
      {
        contactEmail: payload.email,
        registeredName: payload.registeredName,
        adminFullName: `${payload.firstName} ${payload.lastName}`.trim(),
        password: payload.password,
        accountType: 'business',
        ...getSignupSource(),
      },
    );
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
    const [response] = await Promise.all([
      publicAuthClient.post<AuthInitResponse>('/api/v1/userAccount/initAuth'),
      // Desktop needs the frontend origin before any provider button renders.
      // Run it beside this call so it adds no latency of its own.
      ensureDesktopFrontendOrigin(),
    ]);

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
   * Complete email change using the JWT from the verification link
   * (scope: email:validate). Clears client session — user should sign in again.
   */
  async validateEmailChange(token: string): Promise<void> {
    await publicAuthClient.put(
      '/api/v1/userAccount/validateEmailChange',
      {},
      { headers: { Authorization: `Bearer ${token}` } },
    );
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
   * Authenticate using Microsoft tokens obtained client-side (e.g. via MSAL).
   * Sends both `accessToken` and `idToken` to the backend.
   */
  async signInWithMicrosoft(
    credentials: { accessToken: string; idToken: string },
  ): Promise<SignInResponse> {
    return postUserAccountAuthenticate(
      { credentials, method: 'microsoft' },
      getAuthSessionRequestConfig(),
    );
  },
};

export default AuthApi;
