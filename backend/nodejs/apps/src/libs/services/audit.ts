import { getRequestContext } from '../context/request-context';
import {
  EVENT_NAME,
  isPipesHubId,
  Logger,
  shownIdentity,
  SNAKE_TOKEN,
} from './logger.service';

export type AuditOutcome = 'success' | 'failure';

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
}

const INVALID_EVENT = 'audit.invalid_event';

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
    event_name: name,
  });
}
