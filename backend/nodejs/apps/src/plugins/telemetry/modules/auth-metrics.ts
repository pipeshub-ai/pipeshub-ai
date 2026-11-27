import {
  AUTH_METHODS,
  AUTH_OUTCOMES,
  type AuthAttemptMethod,
  type AuthAttemptOutcome,
} from '../../../libs/utils/auth-attempt.utils';
import { metricsBackend } from '../metrics-backend';

/**
 * SEC-D-13 (ADR-OPS-03): every credential a person or a client presents, counted where it is accepted or refused.
 * Labels are two fixed sets, so no email, user, org, token or caller text is ever a series: credential stuffing
 * across many accounts shows as one rising failure rate rather than staying under each account's own limit.
 */
export {
  AUTH_METHODS,
  AUTH_OUTCOMES,
  refusalOutcome,
  signInMethod,
  type AuthAttemptMethod,
  type AuthAttemptOutcome,
} from '../../../libs/utils/auth-attempt.utils';

export const AUTH_ATTEMPTS_METRIC = 'pipeshub_auth_attempts_total';

const attempts = metricsBackend.createCounter({
  name: AUTH_ATTEMPTS_METRIC,
  help: 'Credentials presented to sign in or to call the API, by method and whether they were accepted',
  labelNames: ['outcome', 'method'],
});

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
