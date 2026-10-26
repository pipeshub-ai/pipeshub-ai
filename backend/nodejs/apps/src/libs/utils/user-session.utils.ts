import { Types } from 'mongoose';
import { UserActivities } from '../../modules/auth/schema/userActivities.schema';
import { Users } from '../../modules/user_management/schema/users.schema';
import { Logger } from '../services/logger.service';
import {
  activityEndsSession,
  SESSION_INVALIDATING_ACTIVITIES,
} from './userActivities.utils';

/** The claims of a user session JWT that decide whether it still holds. */
export interface SessionClaims {
  userId?: unknown;
  orgId?: unknown;
  role?: unknown;
  iat?: number;
}

/**
 * A session accepted at handshake, as remembered for re-checking it later.
 * `email` is the one in the token, not the account's current address: rules
 * keyed on an email have to judge the address the session was issued for.
 */
export interface AcceptedSession {
  userId: string;
  orgId: string;
  iat?: number;
  email?: string;
}

type SessionUser = { isDisabled?: boolean; kind?: string };
type SessionActivity = { activityType?: string; createdAt?: Date | null };

export const SESSION_EXPIRED_MESSAGE = 'Session expired, please login again';

function hasValidJwtRole(role: unknown): role is 'admin' | 'member' {
  return role === 'admin' || role === 'member';
}

function sessionRefusal(
  user: SessionUser | null,
  latestActivity: SessionActivity | null,
  issuedAtSeconds: number | undefined,
): string | null {
  if (!user) {
    return 'User not found, please login again';
  }

  // Sessions already handed out have to be stopped too, not only the next
  // sign-in. generateAuthToken refuses to issue one for a disabled account,
  // but a session minted before it was disabled would otherwise keep working
  // until it expired — which for the account an administrator has just
  // switched off is the whole point of switching it off.
  if (user.isDisabled) {
    return 'This account is disabled';
  }

  // A service account has no way to obtain a session in the first place, so
  // one turning up here means something is wrong rather than merely stale.
  if (user.kind === 'service') {
    return 'Service accounts cannot sign in';
  }

  if (latestActivity && activityEndsSession(latestActivity, issuedAtSeconds)) {
    return SESSION_EXPIRED_MESSAGE;
  }

  return null;
}

/**
 * Why a verified session JWT must be refused, or null if it still holds.
 * The HTTP auth middleware and the desktop socket gateway both ask this, so a
 * removed, disabled or logged-out user loses every channel at once.
 */
export async function findSessionRefusal(
  claims: SessionClaims,
  logger: Logger,
): Promise<string | null> {
  // User session JWTs must carry role (admin|member). Legacy tokens without
  // role are rejected so the client re-logins and receives a new token.
  if (!hasValidJwtRole(claims.role)) {
    return SESSION_EXPIRED_MESSAGE;
  }

  const { userId, orgId } = claims;
  // The account and its latest session-invalidating activity are independent
  // reads: asked together they cost one round trip to the database on every
  // authenticated request instead of two.
  const [user, latestActivity] = await Promise.all([
    Users.findOne({
      _id: userId as Types.ObjectId | string,
      isDeleted: false,
    })
      .lean()
      .exec(),
    userId && orgId
      ? UserActivities.findOne({
          userId: userId,
          orgId: orgId,
          isDeleted: false,
          activityType: { $in: [...SESSION_INVALIDATING_ACTIVITIES] },
        })
          .sort({ createdAt: -1 }) // sort by most recent first
          .lean()
          .exec()
          .catch((activityError: unknown): null => {
            logger.error('Failed to fetch user activity', activityError);
            return null;
          })
      : Promise.resolve(null),
  ]);

  return sessionRefusal(user, latestActivity, claims.iat);
}

/**
 * findSessionRefusal for many sessions in two queries, for callers that
 * re-check sessions they already accepted. The role claim was checked when
 * each session was accepted and cannot change, so only the account and its
 * activities are read again. Results are in the order of `sessions`.
 */
export async function findSessionRefusals(
  sessions: ReadonlyArray<AcceptedSession>,
): Promise<Array<string | null>> {
  if (sessions.length === 0) return [];

  const userIds = [...new Set(sessions.map((s) => s.userId))].filter((id) =>
    Types.ObjectId.isValid(id),
  );
  // An activity older than the oldest token cannot end any of these sessions,
  // so only newer ones are read.
  const oldestIssuedAtMs = Math.min(
    ...sessions.map((s) => (s.iat ?? 0) * 1000),
  );

  const [users, activities] = await Promise.all([
    Users.find({ _id: { $in: userIds }, isDeleted: false })
      .select('isDisabled kind')
      .lean()
      .exec(),
    UserActivities.find({
      userId: { $in: userIds },
      isDeleted: false,
      activityType: { $in: [...SESSION_INVALIDATING_ACTIVITIES] },
      createdAt: { $gte: new Date(oldestIssuedAtMs) },
    })
      .select('userId orgId activityType createdAt')
      .sort({ createdAt: -1 })
      .lean()
      .exec(),
  ]);

  const usersById = new Map<string, SessionUser>(
    users.map((u) => [String(u._id), u]),
  );
  const latestActivityBySession = new Map<string, SessionActivity>();
  for (const activity of activities) {
    const key = `${String(activity.orgId)}:${String(activity.userId)}`;
    // Sorted newest first, so the first one seen per user is the latest.
    if (!latestActivityBySession.has(key)) {
      latestActivityBySession.set(key, activity);
    }
  }

  return sessions.map((s) =>
    sessionRefusal(
      usersById.get(s.userId) ?? null,
      latestActivityBySession.get(`${s.orgId}:${s.userId}`) ?? null,
      s.iat,
    ),
  );
}
