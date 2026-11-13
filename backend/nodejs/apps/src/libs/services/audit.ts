import { getRequestContext } from '../context/request-context';
import { AuthMethodType } from '../../modules/auth/types/auth-method';
import {
  EVENT_NAME,
  isPipesHubId,
  Logger,
  shownIdentity,
  SNAKE_TOKEN,
} from './logger.service';

/** `partial`: one step of a multi-step decision passed and more are required. */
export type AuditOutcome = 'success' | 'partial' | 'failure';

export interface AuditFields {
  /** Who acted (a user id). Defaults to the request's verified user. */
  actor?: string;
  /** What was acted on (an id). */
  target?: string;
  /** The target is a user, so it is shown the way user ids are (a provider may hash or hide it). */
  targetIsUser?: boolean;
  /** The org, used only when the request has none yet (before sign-in). */
  org?: string;
  outcome: AuditOutcome;
  /** Why, as a snake_case code (`bad_password`, `locked`), never free text. */
  reason?: string;
  /** The sign-in method (`password`, `google`); anything that is not one is shown as `other`. */
  authMethod?: string;
}

const INVALID_EVENT = 'audit.invalid_event';
const AUTH_METHODS: ReadonlySet<string> = new Set(
  Object.values(AuthMethodType),
);
const REFUSAL = Symbol('auditRefusal');

/** A value that is not an id PipesHub issued is rejected, never cleaned: an email must not reach the line in any form. */
const asId = (value: string | undefined): string | null | undefined =>
  value === undefined ? undefined : isPipesHubId(value) ? value : null;

/** A user id as the provider shows user ids; null when it hides them. */
const shownUser = (userId: string | null | undefined): string | null =>
  typeof userId === 'string'
    ? (shownIdentity({ user_id: userId }).user_id ?? null)
    : null;

/**
 * One INFO line on the `audit` logger for a security event, always with the
 * same keys (actor, target, outcome, reason; missing ones are null), so the
 * events can be filtered and counted. `event` is dotted lowercase
 * (`auth.sign_in`, `user.role_change`). Follows LOG_LEVEL like every line.
 */
export function audit(event: string, fields: AuditFields): void {
  const actorId =
    fields.actor === undefined
      ? getRequestContext()?.userId
      : asId(fields.actor);
  const targetId = asId(fields.target);
  const org = asId(fields.org);
  const invalidId = actorId === null || targetId === null || org === null;
  const reason = invalidId
    ? 'invalid_id'
    : fields.reason === undefined
      ? null
      : SNAKE_TOKEN.test(fields.reason)
        ? fields.reason
        : 'other';
  // The logger fills a missing request org from org_id, shown as the provider decides.
  const name = EVENT_NAME.test(event) ? event : INVALID_EVENT;
  Logger.getInstance({ service: 'audit' }).info(name, {
    ...(typeof org === 'string' && { org_id: org }),
    actor: shownUser(actorId),
    target: fields.targetIsUser ? shownUser(targetId) : (targetId ?? null),
    outcome: fields.outcome,
    reason,
    // The request body names the method, so only a known one is ever printed.
    ...(fields.authMethod !== undefined && {
      auth_method: AUTH_METHODS.has(fields.authMethod)
        ? fields.authMethod
        : 'other',
    }),
    event_name: name,
  });
}

/**
 * Tag a refusal with why it was refused (a snake_case code), on a symbol key:
 * never in a response body, a spread, JSON or a log line. The first tag wins.
 * The handler that ends the request audits it once (auditRefusal).
 */
export function refusal<E extends Error>(error: E, reason: string): E {
  if (!(REFUSAL in error)) {
    Object.defineProperty(error, REFUSAL, {
      value: reason,
      enumerable: false,
    });
  }
  return error;
}

/** The reason this error was refused for. Its cause is not read: a system error wrapping a refusal is not a refusal. */
export function refusalReason(error: unknown): string | undefined {
  if (!(error instanceof Error) || !(REFUSAL in error)) return undefined;
  return (error as unknown as Record<symbol, string>)[REFUSAL];
}

/**
 * One failure line for `event` when `error` is a refusal it may carry. Any other
 * error (a system failure, or a refusal that belongs to another event) writes
 * nothing here; the error middleware logs it.
 */
export function auditRefusal(
  event: string,
  error: unknown,
  who: Pick<AuditFields, 'actor' | 'org' | 'authMethod'>,
  allowed: readonly string[],
): void {
  const reason = refusalReason(error);
  if (reason === undefined || !allowed.includes(reason)) return;
  audit(event, { ...who, outcome: 'failure', reason });
}
