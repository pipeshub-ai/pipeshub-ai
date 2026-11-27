import { BaseError } from '../errors/base.error';

/**
 * How a presented credential is named and how its check ended, for the `auth.attempted` platform event (SEC-D-13).
 * Two fixed sets: no email, user, org, token or caller text is ever part of either.
 */

/** Sign-in steps (`/userAccount/authenticate`), then the bearer credentials the API middleware checks. */
export const AUTH_METHODS = [
  'password',
  'otp',
  'google',
  'microsoft',
  'azure_ad',
  'oauth',
  'github',
  'saml',
  'session',
  'pat',
  'service_token',
  'oauth_token',
  'other',
] as const;
export type AuthAttemptMethod = (typeof AUTH_METHODS)[number];

/** `failure`: the credential was refused (a 4xx); `error`: the check itself failed (a 5xx), not the caller. */
export const AUTH_OUTCOMES = ['success', 'failure', 'error'] as const;
export type AuthAttemptOutcome = (typeof AUTH_OUTCOMES)[number];

const SIGN_IN_METHODS: Readonly<Record<string, AuthAttemptMethod>> = {
  password: 'password',
  otp: 'otp',
  google: 'google',
  microsoft: 'microsoft',
  azureAd: 'azure_ad',
  oauth: 'oauth',
  github: 'github',
  samlSso: 'saml',
};

/** A sign-in step's `method` as the request body names it; anything else is `other`. */
export function signInMethod(raw: unknown): AuthAttemptMethod {
  return typeof raw === 'string' && Object.hasOwn(SIGN_IN_METHODS, raw)
    ? (SIGN_IN_METHODS[raw] as AuthAttemptMethod)
    : 'other';
}

/** How a refusal reads: the caller's credential (4xx) or the check's own failure (anything else). */
export function refusalOutcome(error: unknown): AuthAttemptOutcome {
  return error instanceof BaseError &&
    error.statusCode >= 400 &&
    error.statusCode < 500
    ? 'failure'
    : 'error';
}
