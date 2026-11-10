import { auditRefusal, refusal } from '../../../libs/services/audit';

/** Why an auth decision refused. Shown as `reason=` on the audit line; never shown to the client. */
export type AuthAuditReason =
  | 'unknown_user'
  | 'bad_password'
  | 'no_password'
  | 'bad_otp'
  | 'no_otp'
  | 'otp_expired'
  | 'otp_already_used'
  | 'locked'
  | 'account_disabled'
  | 'service_account'
  | 'account_changed'
  | 'method_not_allowed'
  | 'captcha_failed'
  | 'no_session'
  | 'session_expired'
  | 'sso_token_invalid'
  | 'sso_token_expired'
  | 'sso_wrong_tenant'
  | 'sso_no_email'
  | 'sso_email_mismatch'
  | 'no_account'
  | 'account_removed'
  | 'no_token'
  | 'token_invalid'
  | 'token_expired'
  | 'link_used'
  | 'bad_current_password'
  | 'email_taken'
  | 'session_ended'
  | 'account_inactive'
  | 'no_credentials'
  | 'not_admin';

const TOKEN_REFUSALS = ['no_token', 'token_invalid', 'token_expired'] as const;

/** The refusals each event may carry; a refusal outside its event's list is not that event's decision. */
export const AUTH_AUDIT_REASONS = {
  'auth.sign_in': [
    'unknown_user',
    'bad_password',
    'no_password',
    'bad_otp',
    'no_otp',
    'otp_expired',
    'otp_already_used',
    'locked',
    'account_disabled',
    'service_account',
    'account_changed',
    'method_not_allowed',
    'captcha_failed',
    'no_session',
    'session_expired',
    'sso_token_invalid',
    'sso_token_expired',
    'sso_wrong_tenant',
    'sso_no_email',
    'sso_email_mismatch',
    'no_account',
    'account_removed',
  ],
  'auth.otp.send': ['unknown_user', 'locked'],
  'auth.password_reset.request': ['unknown_user', 'captcha_failed'],
  'auth.password_reset.complete': [
    ...TOKEN_REFUSALS,
    'link_used',
    'locked',
    'unknown_user',
  ],
  'auth.password.change': ['bad_current_password', 'captcha_failed', 'locked'],
  'auth.email.change': [...TOKEN_REFUSALS, 'link_used', 'email_taken'],
  'auth.session.refresh': [
    ...TOKEN_REFUSALS,
    'session_ended',
    'account_inactive',
    'no_credentials',
    'locked',
  ],
  'auth.policy.update': ['not_admin'],
} as const satisfies Record<string, readonly AuthAuditReason[]>;

export type AuthRefusalEvent = keyof typeof AUTH_AUDIT_REASONS;

/** `refusal` with the reason checked at compile time. */
export const authRefusal = <E extends Error>(
  error: E,
  reason: AuthAuditReason,
): E => refusal(error, reason);

/** One failure line for `event` if `error` is one of its refusals. */
export const auditAuthRefusal = (
  event: AuthRefusalEvent,
  error: unknown,
  who: { actor?: string; org?: string; authMethod?: string } = {},
): void => auditRefusal(event, error, who, AUTH_AUDIT_REASONS[event]);
