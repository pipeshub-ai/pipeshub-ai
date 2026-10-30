/**
 * PostgreSQL implementation of the activity repository (`DOC_STORE=postgres`). A row names its organisation and is written in
 * that organisation's unit of work; an attempt made before the person is known to one goes through the identity function
 * for pre-organisation activity. The newest session-ending row is read per type from the index and merged, so the hot read
 * touches a handful of rows however long the history is.
 */
import { ValidationError } from '../../errors/validation.error';
import { stripNul } from '../nul';
import {
  asOrg,
  assertOrg,
  inPreOrgUnit,
  inUnit,
  newObjectId,
  orgForKey,
  oidOf,
} from '../pg-util';
import {
  SESSION_INVALIDATING_ACTIVITIES,
  userActivitiesType,
} from '../../utils/userActivities.utils';
import type { ActivityDto, ActivityRepository, NewActivity } from './types';

const TYPES: ReadonlySet<string> = new Set(Object.values(userActivitiesType));
const MODES: ReadonlySet<string> = new Set([
  'OTP',
  'PASSWORD',
  'AUTH SERVICE',
  'GOOGLE OAUTH',
  'MICROSOFT OAUTH',
  'SSO',
  'AZUREAD OAUTH',
  'OAUTH',
  'GITHUB OAUTH',
]);

interface ActivityRow {
  id: string;
  user_id: string | null;
  email: string | null;
  activity_type: string;
  login_mode: string | null;
  ip_address: string;
  is_deleted: boolean;
  created_at: Date;
  updated_at: Date;
}

const refuse = (what: string): never => {
  throw new ValidationError(what, []);
};

/** The checks the document schema made on every entry: a known type and mode, and an address to go with it. */
export function checkActivity(e: NewActivity): NewActivity {
  if (!TYPES.has(e.activityType))
    refuse(`unknown activity type ${e.activityType}`);
  if (typeof e.ipAddress !== 'string' || e.ipAddress === '')
    refuse('ipAddress is required');
  if (e.loginMode !== undefined && !MODES.has(e.loginMode))
    refuse(`unknown login mode ${e.loginMode}`);
  return stripNul(e, 'activity', 'entry');
}

function dto(r: ActivityRow, orgId: string): ActivityDto {
  const o: Record<string, unknown> = {
    _id: r.id,
    orgId,
    activityType: r.activity_type,
    ipAddress: r.ip_address,
    isDeleted: r.is_deleted,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
  if (r.user_id !== null) o.userId = r.user_id;
  if (r.email !== null) o.email = r.email;
  if (r.login_mode !== null) o.loginMode = r.login_mode;
  return o as unknown as ActivityDto;
}

const COLS =
  'a.id, a.user_id, a.email, a.activity_type, a.login_mode, a.ip_address, a.is_deleted, a.created_at, a.updated_at';

/**
 * An attempt made before the person is known to an organisation is kept in the install's organisation (the bound one, else the
 * oldest that is not removed), as that organisation's own row. With no organisation yet there is nobody to attribute it to: nothing is kept.
 */
export async function recordBeforeOrg(entry: NewActivity): Promise<void> {
  const e = checkActivity({ ...entry });
  const org = await orgForKey(() =>
    inPreOrgUnit({ write: false }, async (k) => {
      const { rows } = await k.query<{ id: string | null }>(
        '/* pipeshub:identity.oldest_org */ SELECT identity.oldest_org() AS id',
      );
      return rows[0]?.id ?? null;
    }),
  );
  if (org === null) return;
  await asOrg(org, () =>
    new PgActivityRepository().recordMany([
      { ...e, orgId: org, email: e.email?.toLowerCase() },
    ]),
  );
}

export class PgActivityRepository implements ActivityRepository {
  async record(entry: NewActivity): Promise<void> {
    await this.recordMany([entry]);
  }

  async recordMany(entries: NewActivity[]): Promise<void> {
    const checked = entries.map(checkActivity);
    const named = checked.filter((e) => e.orgId !== undefined);
    const anonymous = checked.filter((e) => e.orgId === undefined);
    if (named.length > 0) {
      const now = new Date();
      await inUnit(undefined, { write: true }, async (k) => {
        for (const e of named) assertOrg(k, e.orgId as string);
        const userIds = named.map((e) => {
          if (e.userId === undefined) return null;
          return oidOf(e.userId) ?? refuse('userId is not an id');
        });
        await k.query(
          `/* pipeshub:identity.activity_record */ INSERT INTO identity.user_activities
            (org_id, id, user_id, email, activity_type, login_mode, ip_address, details, created_at, updated_at)
            SELECT $1, u.id, u.user_id, u.email, u.activity_type, u.login_mode, u.ip_address, u.details, $8::timestamptz, $8::timestamptz
              FROM unnest($2::text[], $3::text[], $4::text[], $5::text[], $6::text[], $7::text[], $9::text[])
                AS u(id, user_id, email, activity_type, login_mode, ip_address, details)`,
          [
            k.orgId,
            named.map(() => newObjectId()),
            userIds,
            named.map((e) => e.email?.toLowerCase() ?? null),
            named.map((e) => e.activityType),
            named.map((e) => e.loginMode ?? null),
            named.map((e) => e.ipAddress),
            now,
            named.map((e) => e.details ?? null),
          ],
        );
      });
    }
    for (const e of anonymous) {
      if (e.email === undefined || e.email === '')
        refuse('an activity needs an organisation or an address');
      await recordBeforeOrg(e);
    }
  }

  async listSessionEndingSince(
    userIds: string[],
    since: Date,
  ): Promise<ActivityDto[]> {
    const ids = userIds.flatMap((i) => oidOf(i) ?? []);
    if (ids.length === 0) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      const { rows } = await k.query<ActivityRow>(
        `/* pipeshub:identity.activity_session_ending_since */ SELECT ${COLS} FROM identity.user_activities a
          WHERE a.org_id = $1 AND a.user_id = ANY($2::text[]) AND NOT a.is_deleted AND a.activity_type = ANY($3::text[]) AND a.created_at >= $4
          ORDER BY a.created_at DESC, a.id DESC`,
        [k.orgId, ids, [...SESSION_INVALIDATING_ACTIVITIES], since],
      );
      return rows.map((r) => dto(r, k.orgId));
    });
  }

  async latestSessionEnding(
    userId: string,
    orgId: string,
  ): Promise<ActivityDto | null> {
    return this.latest(userId, orgId, [...SESSION_INVALIDATING_ACTIVITIES]);
  }

  async latestPasswordChange(
    userId: string,
    orgId: string,
  ): Promise<ActivityDto | null> {
    return this.latest(userId, orgId, [userActivitiesType.PASSWORD_CHANGED]);
  }

  private async latest(
    userId: string,
    orgId: string,
    types: string[],
  ): Promise<ActivityDto | null> {
    const uid = oidOf(userId);
    if (uid === undefined) return null;
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<ActivityRow>(
        `/* pipeshub:identity.activity_latest */ SELECT ${COLS}
           FROM unnest($3::text[]) AS t(type)
           CROSS JOIN LATERAL (SELECT ${COLS} FROM identity.user_activities a
              WHERE a.org_id = $1 AND a.user_id = $2::text AND NOT a.is_deleted AND a.activity_type = t.type
              ORDER BY a.created_at DESC, a.id DESC LIMIT 1) a
           ORDER BY a.created_at DESC, a.id DESC LIMIT 1`,
        [k.orgId, uid, types],
      );
      return rows[0] ? dto(rows[0], k.orgId) : null;
    });
  }
}
