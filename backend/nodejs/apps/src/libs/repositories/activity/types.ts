/**
 * Activity repository: the append-only record of sign-ins, sign-outs, lockouts,
 * password and role changes. The newest session-ending entry is what every
 * authenticated request checks, so `latestSessionEnding` is the hot read.
 */
export type ActivityType =
  | 'LOGIN'
  | 'LOGOUT'
  | 'LOGIN ATTEMPT'
  | 'OTP GENERATE'
  | 'WRONG OTP'
  | 'WRONG PASSWORD'
  | 'REFRESH TOKEN'
  | 'PASSWORD CHANGED'
  | 'ROLE CHANGED'
  | 'ACCOUNT BLOCKED'
  | 'ACCOUNT DELETED'
  | 'ACCOUNT RESTORED';

export interface NewActivity {
  activityType: string;
  /** Absent for attempts made before the person is known. */
  email?: string;
  userId?: string;
  orgId?: string;
  ipAddress: string | undefined;
  loginMode?: string;
}

export interface ActivityDto {
  _id: string;
  email?: string;
  userId?: string;
  orgId?: string;
  activityType: string;
  loginMode?: string;
  ipAddress: string;
  isDeleted?: boolean;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface ActivityRepository {
  record(entry: NewActivity): Promise<void>;
  /** One write for several entries (a role change reaches every membership). */
  recordMany(entries: NewActivity[]): Promise<void>;
  /** The newest sign-out, password change, role change, lock, removal or restore of this person. */
  latestSessionEnding(
    userId: string,
    orgId: string,
  ): Promise<ActivityDto | null>;
  /**
   * Session-ending entries of these people created at or after `since`, newest first (the caller keeps the first per
   * person and organisation).
   */
  listSessionEndingSince(
    userIds: string[],
    since: Date,
  ): Promise<ActivityDto[]>;
  /** The newest password change of this person. */
  latestPasswordChange(
    userId: string,
    orgId: string,
  ): Promise<ActivityDto | null>;
}
