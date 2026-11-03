import { Logger } from '../services/logger.service';
import { makeOrgContext, runWithOrgContext } from '../context/orgContext';
import { getActivityRepository } from '../repositories/activity';
import { isValidId } from '../repositories/ids';
import { getUserRepository } from '../repositories/user';
import { activityEndsSession } from './userActivities.utils';

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

// The reads below run under the org the token names, as the HTTP middleware's own do.
function asVerified<T>(
  orgId: string | undefined,
  userId: string | undefined,
  fn: () => Promise<T>,
): Promise<T> {
  if (!orgId) return fn();
  return runWithOrgContext(
    makeOrgContext({
      orgId,
      userId: userId || null,
      kind: 'user',
      source: 'jwt',
    }),
    fn,
  );
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
  // Same for a Slack-only account: it only ever holds service-agent tokens.
  if (user.kind === 'slack') {
    return 'This account can only be used from Slack';
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

  const userId = claims.userId as string;
  const orgId = claims.orgId as string;
  // The account and its latest session-invalidating activity are independent
  // reads: asked together they cost one round trip to the database on every
  // authenticated request instead of two.
  const [user, latestActivity] = await asVerified(orgId, userId, () =>
    Promise.all([
      getUserRepository().getLiveById(userId),
      userId && orgId
        ? getActivityRepository()
            .latestSessionEnding(userId, orgId)
            .catch((activityError: unknown): null => {
              logger.error('Failed to fetch user activity', activityError);
              return null;
            })
        : Promise.resolve(null),
    ]),
  );

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
  // Each organisation's sessions are read as that organisation: two queries per organisation (one in practice).
  const byOrg = new Map<string, AcceptedSession[]>();
  for (const s of sessions)
    byOrg.set(s.orgId, [...(byOrg.get(s.orgId) ?? []), s]);
  const refusals = new Map<AcceptedSession, string | null>();
  await Promise.all(
    [...byOrg].map(async ([orgId, group]) => {
      const found = await asVerified(orgId, undefined, () =>
        findSessionRefusalsInOrg(group),
      );
      group.forEach((s, i) => refusals.set(s, found[i] ?? null));
    }),
  );
  return sessions.map((s) => refusals.get(s) ?? null);
}

async function findSessionRefusalsInOrg(
  sessions: ReadonlyArray<AcceptedSession>,
): Promise<Array<string | null>> {
  const userIds = [...new Set(sessions.map((s) => s.userId))].filter((id) =>
    isValidId(id),
  );
  // An activity older than the oldest token cannot end any of these sessions,
  // so only newer ones are read.
  const oldestIssuedAtMs = Math.min(
    ...sessions.map((s) => (s.iat ?? 0) * 1000),
  );

  const [users, activities] = await Promise.all([
    getUserRepository().listLiveFieldsByIds(userIds, ['isDisabled', 'kind']),
    getActivityRepository().listSessionEndingSince(
      userIds,
      new Date(oldestIssuedAtMs),
    ),
  ]);

  const usersById = new Map<string, SessionUser>(
    users.map((u) => [String((u as { _id?: string })._id), u as SessionUser]),
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
