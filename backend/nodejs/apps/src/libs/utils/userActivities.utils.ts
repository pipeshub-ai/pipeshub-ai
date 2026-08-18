export const userActivitiesType = {
    LOGIN: "LOGIN",
    LOGOUT: "LOGOUT",
    OTP_GENERATE: "OTP GENERATE",
    LOGIN_ATTEMPT: "LOGIN ATTEMPT",
    WRONG_PASSWORD: "WRONG PASSWORD",
    WRONG_OTP: "WRONG OTP",
    REFRESH_TOKEN: "REFRESH TOKEN",
    PASSWORD_CHANGED: "PASSWORD CHANGED",
    /** Invalidates JWTs issued before this activity (same effect as password change). */
    ROLE_CHANGED: "ROLE CHANGED",
  };

/** Activity types that invalidate access/refresh tokens issued earlier. */
export const SESSION_INVALIDATING_ACTIVITIES = [
  userActivitiesType.LOGOUT,
  userActivitiesType.PASSWORD_CHANGED,
  userActivitiesType.ROLE_CHANGED,
] as const;