export const userActivitiesType = {
  LOGIN: 'LOGIN',
  LOGOUT: 'LOGOUT',
  OTP_GENERATE: 'OTP GENERATE',
  LOGIN_ATTEMPT: 'LOGIN ATTEMPT',
  WRONG_PASSWORD: 'WRONG PASSWORD',
  WRONG_OTP: 'WRONG OTP',
  REFRESH_TOKEN: 'REFRESH TOKEN',
  PASSWORD_CHANGED: 'PASSWORD CHANGED',
  ROLE_CHANGED: 'ROLE CHANGED',
  ACCOUNT_BLOCKED: 'ACCOUNT BLOCKED',
  ACCOUNT_DELETED: 'ACCOUNT DELETED',
  ACCOUNT_RESTORED: 'ACCOUNT RESTORED',
  ACCOUNT_DISABLED: 'ACCOUNT DISABLED',
  ACCOUNT_ENABLED: 'ACCOUNT ENABLED',
} as const;

export type UserActivityType =
  (typeof userActivitiesType)[keyof typeof userActivitiesType];

/** Every activity type a store must accept: the document schema and the PostgreSQL store both check against this list. */
export const USER_ACTIVITY_TYPES: readonly UserActivityType[] =
  Object.values(userActivitiesType);

export const USER_LOGIN_MODES = [
  'OTP',
  'PASSWORD',
  'AUTH SERVICE',
  'GOOGLE OAUTH',
  'MICROSOFT OAUTH',
  'SSO',
  'AZUREAD OAUTH',
  'OAUTH',
  'GITHUB OAUTH',
] as const;

export type UserLoginMode = (typeof USER_LOGIN_MODES)[number];

export const SESSION_INVALIDATING_ACTIVITIES = [
  userActivitiesType.LOGOUT,
  userActivitiesType.PASSWORD_CHANGED,
  userActivitiesType.ROLE_CHANGED,
  userActivitiesType.ACCOUNT_BLOCKED,
  userActivitiesType.ACCOUNT_DELETED,
  userActivitiesType.ACCOUNT_RESTORED,
  // SEC-AZ-08: a deactivation ends the sessions handed out before it, so a reactivation resumes none of them.
  userActivitiesType.ACCOUNT_DISABLED,
] as const;

// A password change issues the caller's new tokens in its own second, so other
// activities spare tokens issued up to a second before them. A deletion and a
// restore issue none, so they end every token issued in their second or before:
// the restore also ends any token minted while the deletion was still running.
const SESSION_INVALIDATE_TOKEN_DELAY_MS = 1000;

export function activityEndsSession(
  activity: { activityType?: string; createdAt?: Date | null },
  tokenIssuedAtSeconds: number | undefined,
): boolean {
  const issuedAt = tokenIssuedAtSeconds ? tokenIssuedAtSeconds * 1000 : 0;
  const at = activity.createdAt?.getTime() || 0;
  return activity.activityType === userActivitiesType.ACCOUNT_DELETED ||
    activity.activityType === userActivitiesType.ACCOUNT_RESTORED ||
    activity.activityType === userActivitiesType.ACCOUNT_DISABLED
    ? at >= issuedAt
    : at > issuedAt + SESSION_INVALIDATE_TOKEN_DELAY_MS;
}
