import { BaseError } from '../../../errors/base.error';
import { metricsBackend } from '../metrics-backend';

/**
 * SEC-D-13 (ADR-OPS-03): every credential a person or a client presents, counted where it is accepted or refused.
 * Labels are two fixed sets, so no email, user, org, token or caller text is ever a series: credential stuffing
 * across many accounts shows as one rising failure rate rather than staying under each account's own limit.
 */
export const AUTH_ATTEMPTS_METRIC = 'pipeshub_auth_attempts_total';

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

const attempts = metricsBackend.createCounter({
  name: AUTH_ATTEMPTS_METRIC,
  help: 'Credentials presented to sign in or to call the API, by method and whether they were accepted',
  labelNames: ['outcome', 'method'],
});

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

/** A method an edition adds: lowercase letters and underscores, so a label is a name, never caller text. */
const EDITION_METHOD = /^[a-z][a-z_]{1,31}$/;
const editionMethods = new Set<string>();

/**
 * Sign-in methods an edition offers beyond this build's, added to the label set when its module loads. The set stays
 * fixed for the process: a name is checked here, once, never per attempt. Adding a name again is a no-op.
 */
export function addAuthAttemptMethods(names: readonly string[]): void {
  for (const name of names) {
    if (
      !EDITION_METHOD.test(name) ||
      (AUTH_METHODS as readonly string[]).includes(name)
    ) {
      throw new Error(
        `Not a new sign-in method label: ${JSON.stringify(name)}`,
      );
    }
  }
  for (const name of names) editionMethods.add(name);
}

const knownMethod = (method: string): boolean =>
  (AUTH_METHODS as readonly string[]).includes(method) ||
  editionMethods.has(method);

/** Never throws: a metrics fault must not turn a refusal into an acceptance, or an acceptance into a 500. */
export function recordAuthAttempt(
  method: AuthAttemptMethod | (string & {}),
  outcome: AuthAttemptOutcome,
): void {
  try {
    attempts.inc({
      outcome: (AUTH_OUTCOMES as readonly string[]).includes(outcome)
        ? outcome
        : 'error',
      method:
        typeof method === 'string' && knownMethod(method) ? method : 'other',
    });
  } catch {
    // Counted nowhere rather than answered differently.
  }
}
