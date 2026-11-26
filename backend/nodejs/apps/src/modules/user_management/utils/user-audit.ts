import { isValidId } from '../../../libs/repositories/ids';
import {
  audit,
  AuditFields,
  auditRefusal,
  refusal,
} from '../../../libs/services/audit';

/** Why a user change was refused. Shown as `reason=` on the audit line; never shown to the client. */
export type UserAuditReason =
  | 'not_admin'
  | 'last_admin'
  | 'admin_limit'
  | 'email_taken'
  | 'password_not_allowed'
  | 'not_self'
  | 'not_member'
  | 'name_taken'
  | 'restoring'
  | 'not_found'
  | 'account_disabled'
  | 'scope_denied'
  | 'token_limit'
  | 'not_owner'
  | 'protected_team';

/** The refusals each event may carry; a refusal outside its event's list is not that event's decision. */
export const USER_AUDIT_REASONS = {
  'user.create': ['email_taken', 'password_not_allowed', 'admin_limit'],
  'user.invite': ['not_admin', 'admin_limit'],
  'user.role.change': ['not_admin', 'last_admin', 'admin_limit'],
  'user.update': ['not_admin'],
  'user.email_change.request': ['not_self', 'email_taken'],
  'user.delete': ['not_member'],
  'service_account.create': ['name_taken'],
  'service_account.update': ['restoring', 'not_found'],
  'service_account.delete': ['not_found'],
  'service_account.token.create': [
    'account_disabled',
    'scope_denied',
    'token_limit',
    'not_found',
  ],
  'service_account.token.revoke': ['not_found'],
  'team.create': ['name_taken'],
  'team.update': ['not_owner', 'not_found', 'name_taken'],
  'team.delete': ['not_owner', 'not_found', 'protected_team'],
} as const satisfies Record<string, readonly UserAuditReason[]>;

export type UserRefusalEvent = keyof typeof USER_AUDIT_REASONS;
export type UserEvent = UserRefusalEvent | 'user.restore';

/** `refusal` with the reason checked at compile time. */
export const userRefusal = <E extends Error>(
  error: E,
  reason: UserAuditReason,
): E => refusal(error, reason);

/** One failure line for `event` if `error` is one of its refusals. */
export const auditUserRefusal = (
  event: UserRefusalEvent,
  error: unknown,
  who: Pick<
    AuditFields,
    'target' | 'targetIsUser' | 'targetIsTeam' | 'key'
  > = {},
): void => auditRefusal(event, error, who, USER_AUDIT_REASONS[event]);

/** A change made to one user account; the target is shown as a user id (a provider may hash it). */
export const auditUserChange = (
  event: UserEvent,
  target: string,
  reason?: string,
): void =>
  audit(event, { target, targetIsUser: true, outcome: 'success', reason });

/**
 * The requested group ids that `find` returns a group for. A value that is not
 * an id is not looked up: it counts as missing, and the write that follows
 * treats it exactly as before.
 */
export async function foundGroupIds(
  groupIds: readonly string[],
  find: (id: string) => Promise<unknown>,
): Promise<Set<string>> {
  const found = new Set<string>();
  for (const id of new Set(groupIds)) {
    if (isValidId(id) && (await find(id))) found.add(id);
  }
  return found;
}

/**
 * One line per user × group: a group not in `found` is a `not_found` failure;
 * any other pair is a success, with `unchangedReason` when the store changed nothing.
 */
export function auditMemberships(
  event: 'group.member.add' | 'group.member.remove',
  userIds: readonly string[],
  groupIds: readonly string[],
  found: ReadonlySet<string>,
  unchangedReason?: 'already_member' | 'not_member',
): void {
  for (const group of new Set(groupIds)) {
    for (const target of new Set(userIds)) {
      const exists = found.has(group);
      audit(event, {
        target,
        targetIsUser: true,
        group,
        outcome: exists ? 'success' : 'failure',
        reason: exists ? unchangedReason : 'not_found',
      });
    }
  }
}

/**
 * One success line per user added to, removed from or given a new role on a
 * team: the target is the user, `team_id` the team. Entries that are not
 * strings (or are empty) are skipped, as the team service skips them.
 */
export function auditTeamMembers(
  event: 'team.member.add' | 'team.member.remove' | 'team.member.role.change',
  team: string,
  userIds: readonly unknown[],
): void {
  for (const target of new Set(userIds)) {
    if (typeof target !== 'string' || target === '') continue;
    audit(event, { target, targetIsUser: true, team, outcome: 'success' });
  }
}
