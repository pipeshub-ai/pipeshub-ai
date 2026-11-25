/**
 * Shared types for unauthenticated login flows (public auth + userAccount OTP helpers).
 */

export type AuthMethod =
  | 'password'
  | 'otp'
  | 'google'
  | 'microsoft';

export interface SignInResponse {
  status?: string;
  nextStep?: number;
  allowedMethods?: AuthMethod[];
  authProviders?: Record<string, Record<string, string>>;
  accessToken?: string;
  /** Only for a client that did not ask for the cookie (the desktop app). */
  refreshToken?: string;
  /** `cookie` when the refresh token was set as the HttpOnly session cookie. */
  session?: 'cookie';
  user?: {
    id: string;
    name?: string;
    email?: string;
    phone?: string;
    created_at?: string;
    updated_at?: string;
  };
  message?: string;
}
