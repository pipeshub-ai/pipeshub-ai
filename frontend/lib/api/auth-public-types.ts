/**
 * Shared types for unauthenticated login flows (public auth + userAccount OTP helpers).
 */

export type AuthMethod =
  | 'password'
  | 'otp'
  | 'samlSso'
  | 'google'
  | 'microsoft'
  | 'azureAd'
  | 'oauth';

export interface SignInResponse {
  status?: string;
  nextStep?: number;
  allowedMethods?: AuthMethod[];
  authProviders?: Record<string, Record<string, string>>;
  accessToken?: string;
  refreshToken?: string;
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
