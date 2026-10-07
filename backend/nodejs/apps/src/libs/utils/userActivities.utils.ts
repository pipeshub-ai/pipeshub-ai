export const userActivitiesType = {
    LOGIN: "LOGIN",
    LOGOUT: "LOGOUT",
    OTP_GENERATE: "OTP GENERATE",
    LOGIN_ATTEMPT: "LOGIN ATTEMPT",
    WRONG_PASSWORD: "WRONG PASSWORD",
    WRONG_OTP: "WRONG OTP",
    REFRESH_TOKEN: "REFRESH TOKEN",
    PASSWORD_CHANGED: "PASSWORD CHANGED",
    ROLE_CHANGED: "ROLE CHANGED",
    ACCOUNT_BLOCKED: "ACCOUNT BLOCKED",
    ACCOUNT_DELETED: "ACCOUNT DELETED",
    ACCOUNT_RESTORED: "ACCOUNT RESTORED",
  };

export const SESSION_INVALIDATING_ACTIVITIES = [
  userActivitiesType.LOGOUT,
  userActivitiesType.PASSWORD_CHANGED,
  userActivitiesType.ROLE_CHANGED,
  userActivitiesType.ACCOUNT_BLOCKED,
  userActivitiesType.ACCOUNT_DELETED,
  userActivitiesType.ACCOUNT_RESTORED,
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
    activity.activityType === userActivitiesType.ACCOUNT_RESTORED
    ? at >= issuedAt
    : at > issuedAt + SESSION_INVALIDATE_TOKEN_DELAY_MS;
}
