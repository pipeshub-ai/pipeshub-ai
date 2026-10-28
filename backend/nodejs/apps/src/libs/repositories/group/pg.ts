/**
 * PostgreSQL implementation of the group repository (`DOC_STORE=postgres`). Members are rows of their own, in the order they
 * were added; a name is unique among an organisation's live groups (a partial unique index), so a duplicate arrives as a
 * unique-key error naming `name`.
 */
import { stripNul } from '../nul';
import {
  Params,
  assertOrg,
  inUnit,
  likeEscape,
  newObjectId,
  oidOf,
  oidsOf,
} from '../pg-util';
import { nextSlug } from '../slug/pg';
import type {
  GroupDto,
  GroupListQuery,
  GroupRepository,
  GroupStat,
  GroupSummaryDto,
  GroupType,
} from './types';

interface GroupRow {
  id: string;
  org_id: string;
  slug: string;
  name: string;
  type: GroupType;
  is_deleted: boolean;
  deleted_by: string | null;
  version: number;
  created_at: Date;
  updated_at: Date;
  users: string[];
}

const COLS = `g.id, g.org_id, g.slug, g.name, g.type, g.is_deleted, g.deleted_by, g.version, g.created_at, g.updated_at,
  COALESCE((SELECT array_agg(m.user_id::text ORDER BY m.pos, m.user_id) FROM identity.user_group_members m WHERE m.org_id = g.org_id AND m.group_id = g.id), '{}') AS users`;

function dto(r: GroupRow): GroupDto {
  return {
    _id: r.id,
    slug: r.slug,
    name: r.name,
    type: r.type,
    orgId: r.org_id,
    users: r.users,
    isDeleted: r.is_deleted,
    ...(r.deleted_by !== null && { deletedBy: r.deleted_by }),
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    __v: r.version,
  };
}

const summary = (r: GroupRow): GroupSummaryDto => ({
  _id: r.id,
  name: r.name,
  type: r.type,
});

/** Adds the people at the end of each group's member list (a person already in a group keeps their place). */
const ADD_MEMBERS = `INSERT INTO identity.user_group_members (org_id, group_id, user_id, pos)
  SELECT $1, g.id, u.uid, COALESCE((SELECT max(m.pos) FROM identity.user_group_members m WHERE m.org_id = $1 AND m.group_id = g.id), -1) + u.ord::int
    FROM unnest($2::text[]) AS g(id) CROSS JOIN unnest($3::text[]) WITH ORDINALITY AS u(uid, ord)
  ON CONFLICT DO NOTHING`;

export class PgGroupRepository implements GroupRepository {
  async list(
    orgId: string,
    q: GroupListQuery,
  ): Promise<{ rows: GroupDto[]; total: number }> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const conds = [`g.org_id = ${p.add(k.orgId)}`, 'NOT g.is_deleted'];
      if (q.search !== undefined && q.search !== '')
        conds.push(
          `g.name ILIKE ${p.add(`%${likeEscape(q.search)}%`)} ESCAPE '\\'`,
        );
      if (q.createdAfter !== undefined && q.createdAfter !== '')
        conds.push(`g.created_at >= ${p.add(new Date(q.createdAfter))}`);
      if (q.createdBefore !== undefined && q.createdBefore !== '') {
        const end = new Date(q.createdBefore);
        end.setHours(23, 59, 59, 999);
        conds.push(`g.created_at <= ${p.add(end)}`);
      }
      const where = conds.join(' AND ');
      const count = await k.query<{ n: string }>(
        `/* pipeshub:identity.group_list_count */ SELECT count(*) AS n FROM identity.user_groups g WHERE ${where}`,
        p.values,
      );
      const page = await k.query<GroupRow>(
        `/* pipeshub:identity.group_list */ SELECT ${COLS} FROM identity.user_groups g WHERE ${where} ORDER BY g.id LIMIT ${p.add(q.limit)} OFFSET ${p.add(q.skip)}`,
        p.values,
      );
      return { rows: page.rows.map(dto), total: Number(count.rows[0]?.n ?? 0) };
    });
  }

  private async one(
    tag: string,
    orgId: string,
    id: string,
    live: boolean,
  ): Promise<GroupDto | null> {
    const oid = oidOf(id);
    if (oid === undefined) return null;
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<GroupRow>(
        `/* pipeshub:identity.${tag} */ SELECT ${COLS} FROM identity.user_groups g WHERE g.org_id = $1 AND g.id = $2::text${live ? ' AND NOT g.is_deleted' : ''}`,
        [k.orgId, oid],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  get(orgId: string, id: string): Promise<GroupDto | null> {
    return this.one('group_get', orgId, id, false);
  }

  getLive(orgId: string, id: string): Promise<GroupDto | null> {
    return this.one('group_get_live', orgId, id, true);
  }

  async nameTaken(
    orgId: string,
    name: string,
    exceptId?: string,
  ): Promise<boolean> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query(
        '/* pipeshub:identity.group_name_taken */ SELECT 1 FROM identity.user_groups g WHERE g.org_id = $1 AND g.name = $2 AND NOT g.is_deleted AND ($3::text IS NULL OR g.id <> $3::text) LIMIT 1',
        [
          k.orgId,
          stripNul(name, 'group', 'name'),
          exceptId === undefined ? null : (oidOf(exceptId) ?? ''),
        ],
      );
      return rows.length > 0;
    });
  }

  async create(
    orgId: string,
    input: { name: string; type: GroupType },
  ): Promise<GroupDto> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const now = new Date();
      const { rows } = await k.query<GroupRow>(
        `/* pipeshub:identity.group_create */ INSERT INTO identity.user_groups AS g (org_id, id, slug, name, type, created_at, updated_at)
          VALUES ($1, $2, $3, $4, $5, $6, $6) RETURNING ${COLS}`,
        [
          k.orgId,
          newObjectId(),
          await nextSlug(k, 'UserGroup'),
          stripNul(input.name, 'group', 'name'),
          input.type,
          now,
        ],
      );
      return dto(rows[0] as GroupRow);
    });
  }

  async rename(
    orgId: string,
    id: string,
    name: string,
  ): Promise<GroupDto | null> {
    const oid = oidOf(id);
    if (oid === undefined) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<GroupRow>(
        `/* pipeshub:identity.group_rename */ UPDATE identity.user_groups g SET name = $3, updated_at = $4 WHERE g.org_id = $1 AND g.id = $2::text AND NOT g.is_deleted RETURNING ${COLS}`,
        [k.orgId, oid, stripNul(name, 'group', 'name'), new Date()],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  async softDelete(
    orgId: string,
    id: string,
    deletedBy?: string,
  ): Promise<GroupDto | null> {
    const oid = oidOf(id);
    if (oid === undefined) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<GroupRow>(
        `/* pipeshub:identity.group_soft_delete */ UPDATE identity.user_groups g SET is_deleted = true, deleted_by = $3, updated_at = $4
          WHERE g.org_id = $1 AND g.id = $2::text AND NOT g.is_deleted RETURNING ${COLS}`,
        [
          k.orgId,
          oid,
          deletedBy === undefined ? null : (oidOf(deletedBy) ?? null),
          new Date(),
        ],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  async addMembers(
    orgId: string,
    groupIds: string[],
    userIds: string[],
  ): Promise<number> {
    const groups = oidsOf(groupIds);
    const users = oidsOf(userIds);
    if (groups.length === 0) return 0;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const live = await k.query<{ id: string }>(
        '/* pipeshub:identity.group_touch_live */ UPDATE identity.user_groups g SET updated_at = $3 WHERE g.org_id = $1 AND g.id = ANY($2::text[]) AND NOT g.is_deleted RETURNING g.id',
        [k.orgId, groups, new Date()],
      );
      if (live.rows.length > 0 && users.length > 0)
        await k.query(
          `/* pipeshub:identity.group_members_add */ ${ADD_MEMBERS}`,
          [k.orgId, live.rows.map((r) => r.id), users],
        );
      return live.rows.length;
    });
  }

  async removeMembers(
    orgId: string,
    groupIds: string[],
    userIds: string[],
  ): Promise<number> {
    const groups = oidsOf(groupIds);
    const users = oidsOf(userIds);
    if (groups.length === 0) return 0;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const live = await k.query<{ id: string }>(
        '/* pipeshub:identity.group_touch_live */ UPDATE identity.user_groups g SET updated_at = $3 WHERE g.org_id = $1 AND g.id = ANY($2::text[]) AND NOT g.is_deleted RETURNING g.id',
        [k.orgId, groups, new Date()],
      );
      if (live.rows.length > 0 && users.length > 0)
        await k.query(
          '/* pipeshub:identity.group_members_remove */ DELETE FROM identity.user_group_members m WHERE m.org_id = $1 AND m.group_id = ANY($2::text[]) AND m.user_id = ANY($3::text[])',
          [k.orgId, live.rows.map((r) => r.id), users],
        );
      return live.rows.length;
    });
  }

  async addMemberToGroups(
    orgId: string,
    groupIds: string[],
    userId: string,
  ): Promise<void> {
    const groups = oidsOf(groupIds);
    const uid = oidOf(userId);
    if (groups.length === 0 || uid === undefined) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const found = await k.query<{ id: string }>(
        '/* pipeshub:identity.group_touch_any */ UPDATE identity.user_groups g SET updated_at = $3 WHERE g.org_id = $1 AND g.id = ANY($2::text[]) RETURNING g.id',
        [k.orgId, groups, new Date()],
      );
      if (found.rows.length > 0)
        await k.query(
          `/* pipeshub:identity.group_members_add */ ${ADD_MEMBERS}`,
          [k.orgId, found.rows.map((r) => r.id), [uid]],
        );
    });
  }

  async addToEveryone(
    orgId: string,
    userId: string,
    opts: { liveOnly?: boolean } = {},
  ): Promise<void> {
    const uid = oidOf(userId);
    if (uid === undefined) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      // As on MongoDB, a removed "everyone" group is left alone only when the caller asks (`liveOnly`).
      const found = await k.query<{ id: string }>(
        `/* pipeshub:identity.group_everyone */ UPDATE identity.user_groups g SET updated_at = $2 WHERE g.org_id = $1 AND g.id = (SELECT x.id FROM identity.user_groups x WHERE x.org_id = $1 AND x.type = 'everyone'${opts.liveOnly ? ' AND NOT x.is_deleted' : ''} ORDER BY x.id LIMIT 1) RETURNING g.id`,
        [k.orgId, new Date()],
      );
      if (found.rows.length > 0)
        await k.query(
          `/* pipeshub:identity.group_members_add */ ${ADD_MEMBERS}`,
          [k.orgId, found.rows.slice(0, 1).map((r) => r.id), [uid]],
        );
    });
  }

  async removeFromEveryone(orgId: string, userId: string): Promise<void> {
    const uid = oidOf(userId);
    if (uid === undefined) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        "/* pipeshub:identity.group_leave_everyone */ DELETE FROM identity.user_group_members m USING identity.user_groups g WHERE m.org_id = $1 AND g.org_id = $1 AND g.id = m.group_id AND g.type = 'everyone' AND m.user_id = $2::text",
        [k.orgId, uid],
      );
    });
  }

  async removeFromAll(orgId: string, userId: string): Promise<void> {
    const uid = oidOf(userId);
    if (uid === undefined) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        '/* pipeshub:identity.group_leave_all */ DELETE FROM identity.user_group_members m WHERE m.org_id = $1 AND m.user_id = $2::text',
        [k.orgId, uid],
      );
    });
  }

  async groupsOfUser(
    orgId: string,
    userId: string,
  ): Promise<GroupSummaryDto[]> {
    const uid = oidOf(userId);
    if (uid === undefined) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<GroupRow>(
        `/* pipeshub:identity.groups_of_user */ SELECT g.id, g.org_id, g.slug, g.name, g.type, g.is_deleted, g.deleted_by, g.version, g.created_at, g.updated_at, '{}'::text[] AS users
           FROM identity.user_group_members m JOIN identity.user_groups g ON g.org_id = m.org_id AND g.id = m.group_id
          WHERE m.org_id = $1 AND m.user_id = $2::text AND NOT g.is_deleted ORDER BY g.id`,
        [k.orgId, uid],
      );
      return rows.map(summary);
    });
  }

  async memberIdsOfTypes(orgId: string, types: string[]): Promise<string[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ user_id: string }>(
        `/* pipeshub:identity.group_member_ids_of_types */ SELECT m.user_id::text AS user_id FROM identity.user_groups g
           JOIN identity.user_group_members m ON m.org_id = g.org_id AND m.group_id = g.id
          WHERE g.org_id = $1 AND g.type = ANY($2::text[]) AND NOT g.is_deleted ORDER BY g.id, m.pos, m.user_id`,
        [k.orgId, types],
      );
      return rows.map((r) => r.user_id);
    });
  }

  async summariesForUsers(
    orgId: string,
    userIds: string[],
  ): Promise<Array<GroupSummaryDto & { users: string[] }>> {
    const users = oidsOf(userIds);
    if (users.length === 0) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<GroupRow>(
        `/* pipeshub:identity.groups_for_users */ SELECT ${COLS} FROM identity.user_groups g
          WHERE g.org_id = $1 AND NOT g.is_deleted
            AND EXISTS (SELECT 1 FROM identity.user_group_members m WHERE m.org_id = $1 AND m.group_id = g.id AND m.user_id = ANY($2::text[]))
          ORDER BY g.id`,
        [k.orgId, users],
      );
      return rows.map((r) => ({ ...summary(r), users: r.users }));
    });
  }

  async stats(orgId: string): Promise<GroupStat[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{
        name: string;
        count: string;
        total: string;
        avg: number;
      }>(
        `/* pipeshub:identity.group_stats */ SELECT s.name, count(*) AS count, sum(s.n) AS total, avg(s.n)::float8 AS avg
           FROM (SELECT g.name, (SELECT count(*) FROM identity.user_group_members m WHERE m.org_id = g.org_id AND m.group_id = g.id) AS n
                   FROM identity.user_groups g WHERE g.org_id = $1 AND NOT g.is_deleted) s GROUP BY s.name ORDER BY s.name`,
        [k.orgId],
      );
      return rows.map((r) => ({
        _id: r.name,
        count: Number(r.count),
        totalUsers: Number(r.total),
        avgUsers: r.avg,
      }));
    });
  }
}
