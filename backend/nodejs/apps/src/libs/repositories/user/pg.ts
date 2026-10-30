/**
 * PostgreSQL implementation of the user repository (`DOC_STORE=postgres`). Every statement names the unit's organisation;
 * a call that carries an `orgId` must agree with it. Calls that name only an account id (a signed-in session, a token)
 * read the organisation the request is running as. An address is unique inside the organisation, removed accounts included.
 * The last-admin rule is serialised by locking the organisation row (`lockOrgAdminChanges`).
 */
import {
  assertReservedEmailDomainBelongsToServiceAccount,
  assertServiceAccountRole,
} from '../../../modules/user_management/constants/service-account.constants';
import { stripNul } from '../nul';
import { ValidationError } from '../../errors/validation.error';
import {
  Params,
  assertOrg,
  fill,
  inUnit,
  likeEscape,
  newObjectId,
  oidOf,
  oidsOf,
  type KernelTx as PgKernelTx,
} from '../pg-util';
import { getIdentityPort } from '../identity';
import { nextSlug } from '../slug/pg';
import type { Tx } from '../uow';
import type {
  DirectoryQuery,
  MemberSummaryDto,
  RestoreServiceAccountInput,
  UserAccountState,
  UserAuthSnapshot,
  UserDto,
  UserFields,
  UserKind,
  UserRepository,
  UserRole,
  UserRoleSnapshot,
  UserWithGroupsDto,
} from './types';

interface UserRow {
  id: string;
  org_id: string;
  slug: string;
  full_name: string | null;
  first_name: string | null;
  last_name: string | null;
  middle_name: string | null;
  email: string;
  mobile: string | null;
  has_logged_in: boolean;
  designation: string | null;
  kind: UserKind;
  description: string | null;
  slack_user_id: string | null;
  slack_team_id: string | null;
  is_disabled: boolean;
  restore_op_id: string | null;
  role: UserRole;
  address: Record<string, unknown> | null;
  is_deleted: boolean;
  deleted_by: string | null;
  version: number;
  created_at: Date;
  updated_at: Date;
}

const COLS = `u.id, u.org_id, u.slug, u.full_name, u.first_name, u.last_name, u.middle_name, u.email, u.mobile, u.has_logged_in, u.designation, u.kind,
  u.description, u.slack_user_id, u.slack_team_id, u.is_disabled, u.restore_op_id, u.role, u.address, u.is_deleted, u.deleted_by, u.version, u.created_at, u.updated_at`;

function dto(r: UserRow): UserDto {
  const o: Record<string, unknown> = {
    _id: r.id,
    orgId: r.org_id,
    slug: r.slug,
    email: r.email,
    hasLoggedIn: r.has_logged_in,
    kind: r.kind,
    isDisabled: r.is_disabled,
    role: r.role,
    isDeleted: r.is_deleted,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    __v: r.version,
  };
  if (r.full_name !== null) o.fullName = r.full_name;
  if (r.first_name !== null) o.firstName = r.first_name;
  if (r.last_name !== null) o.lastName = r.last_name;
  if (r.middle_name !== null) o.middleName = r.middle_name;
  if (r.mobile !== null) o.mobile = r.mobile;
  if (r.designation !== null) o.designation = r.designation;
  if (r.description !== null) o.description = r.description;
  if (r.slack_user_id !== null) o.slackUserId = r.slack_user_id;
  if (r.slack_team_id !== null) o.slackTeamId = r.slack_team_id;
  if (r.restore_op_id !== null) o.restoreOpId = r.restore_op_id;
  if (r.address !== null) o.address = r.address;
  if (r.deleted_by !== null) o.deletedBy = r.deleted_by;
  return o as unknown as UserDto;
}

/** `_id` always, then the named fields that are set (a selection of fields leaves out what is not stored). */
function pick<K extends keyof UserDto>(
  r: UserRow,
  fields: K[],
): Pick<UserDto, K> {
  const full = dto(r) as unknown as Record<string, unknown>;
  const out: Record<string, unknown> = { _id: full._id };
  for (const f of fields)
    if (full[f as string] !== undefined) out[f as string] = full[f as string];
  return out as unknown as Pick<UserDto, K>;
}

/** Fields a caller may write and the column each lands in; the document schema trimmed these strings. */
const TRIMMED = new Set([
  'fullName',
  'firstName',
  'lastName',
  'middleName',
  'designation',
  'description',
]);
const COLUMNS: Record<string, string> = {
  fullName: 'full_name',
  firstName: 'first_name',
  lastName: 'last_name',
  middleName: 'middle_name',
  email: 'email',
  mobile: 'mobile',
  hasLoggedIn: 'has_logged_in',
  designation: 'designation',
  role: 'role',
  address: 'address',
  kind: 'kind',
  description: 'description',
  slackUserId: 'slack_user_id',
  slackTeamId: 'slack_team_id',
  isDisabled: 'is_disabled',
  isDeleted: 'is_deleted',
};
const NOT_NULL = new Set([
  'email',
  'hasLoggedIn',
  'role',
  'kind',
  'isDisabled',
  'isDeleted',
]);

function value(field: string, v: unknown): unknown {
  if (v === undefined || v === null) return null;
  if (field === 'address') return JSON.stringify(v);
  if (typeof v === 'string') {
    const s = stripNul(v, 'user', field);
    if (field === 'email') return s.toLowerCase();
    return TRIMMED.has(field) ? s.trim() : s;
  }
  return v;
}

/** The writable fields present in `fields`, with their column values (a field present and `undefined` clears the column). */
function writable(fields: UserFields): Array<[string, string, unknown]> {
  const out: Array<[string, string, unknown]> = [];
  for (const [f, col] of Object.entries(COLUMNS)) {
    if (!Object.prototype.hasOwnProperty.call(fields, f)) continue;
    const v = value(f, fields[f]);
    if (v === null && NOT_NULL.has(f)) continue;
    out.push([f, col, v]);
  }
  return out;
}

const rule = (kind: unknown, role: unknown, email: unknown): void => {
  assertServiceAccountRole(
    typeof kind === 'string' ? kind : undefined,
    typeof role === 'string' ? role : undefined,
  );
  assertReservedEmailDomainBelongsToServiceAccount(
    typeof kind === 'string' ? kind : undefined,
    typeof email === 'string' ? email : undefined,
  );
};

async function insertUser(
  k: PgKernelTx,
  fields: UserFields,
  now: Date,
): Promise<UserDto> {
  const cols = writable(fields);
  const has = (f: string): unknown => cols.find(([name]) => name === f)?.[2];
  const email = has('email');
  if (typeof email !== 'string' || email === '')
    throw new ValidationError('Email required', []);
  rule(has('kind') ?? 'human', has('role') ?? 'member', email);
  const slug = await nextSlug(k, 'User');
  const names = [
    'org_id',
    'id',
    'slug',
    'created_at',
    'updated_at',
    ...cols.map(([, c]) => c),
  ];
  const values = [
    k.orgId,
    newObjectId(),
    slug,
    now,
    now,
    ...cols.map(([, , v]) => v),
  ];
  const marks = names.map(
    (n, i) => `$${String(i + 1)}${n === 'address' ? '::jsonb' : ''}`,
  );
  const { rows } = await k.query<UserRow>(
    fill(
      '/* pipeshub:identity.user_create */ INSERT INTO identity.users AS u (@{NAMES}) VALUES (@{MARKS}) RETURNING @{COLS}',
      { NAMES: names.join(', '), MARKS: marks.join(', '), COLS },
    ),
    values,
  );
  return dto(rows[0] as UserRow);
}

function search(p: Params, text: string): string {
  const pattern = `%${likeEscape(text)}%`;
  const ref = p.add(pattern);
  return `(u.full_name ILIKE ${ref} ESCAPE '\\' OR u.email ILIKE ${ref} ESCAPE '\\')`;
}

export class PgUserRepository implements UserRepository {
  private one(
    sql: string,
    params: unknown[],
    orgId: string | undefined,
  ): Promise<UserDto | null> {
    return inUnit(undefined, { write: false }, async (k) => {
      if (orgId !== undefined) assertOrg(k, orgId);
      const { rows } = await k.query<UserRow>(fill(sql, { COLS }), [
        k.orgId,
        ...params,
      ]);
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  private async rowsOf(
    sql: string,
    orgId: string | undefined,
    params: unknown[],
  ): Promise<UserRow[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      if (orgId !== undefined) assertOrg(k, orgId);
      const { rows } = await k.query<UserRow>(fill(sql, { COLS }), [
        k.orgId,
        ...params,
      ]);
      return rows;
    });
  }

  // ---- single account
  async getLive(orgId: string, id: string): Promise<UserDto | null> {
    const oid = oidOf(id);
    return oid === undefined
      ? null
      : this.one(
          '/* pipeshub:identity.user_get_live */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text AND NOT u.is_deleted LIMIT 1',
          [oid],
          orgId,
        );
  }

  getForEdit(orgId: string, id: string): Promise<UserDto | null> {
    return this.getLive(orgId, id);
  }

  async getEmail(
    orgId: string,
    id: string,
  ): Promise<Pick<UserDto, 'email'> | null> {
    const u = await this.getLive(orgId, id);
    const out: Pick<UserDto, 'email'> & { _id: string } = {
      _id: u?._id ?? '',
      email: u?.email ?? '',
    };
    return u ? out : null;
  }

  async getAny(orgId: string, id: string): Promise<UserDto | null> {
    const oid = oidOf(id);
    return oid === undefined
      ? null
      : this.one(
          '/* pipeshub:identity.user_get_any */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text LIMIT 1',
          [oid],
          orgId,
        );
  }

  async getLiveById(id: string): Promise<UserDto | null> {
    const oid = oidOf(id);
    return oid === undefined
      ? null
      : this.one(
          '/* pipeshub:identity.user_get_live_by_id */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text AND NOT u.is_deleted LIMIT 1',
          [oid],
          undefined,
        );
  }

  async getAccountState(id: string): Promise<UserAccountState | null> {
    const u = await this.getLiveById(id);
    return u ? { kind: u.kind, isDisabled: u.isDisabled } : null;
  }

  async getAuthSnapshot(
    orgId: string,
    id: string,
  ): Promise<UserAuthSnapshot | null> {
    const u = await this.getLive(orgId, id);
    if (!u) return null;
    return {
      _id: u._id,
      email: u.email,
      ...(u.fullName !== undefined && { fullName: u.fullName }),
      role: u.role,
      isDisabled: u.isDisabled,
      kind: u.kind,
    };
  }

  async getRoleSnapshot(
    orgId: string,
    id: string,
  ): Promise<UserRoleSnapshot | null> {
    const u = await this.getLive(orgId, id);
    return u ? { role: u.role, kind: u.kind } : null;
  }

  async getByEmail(email: string): Promise<UserDto | null> {
    return this.one(
      '/* pipeshub:identity.user_get_by_email */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 LIMIT 1',
      [stripNul(email, 'user', 'email').toLowerCase()],
      undefined,
    );
  }

  async getLiveFields<K extends keyof UserDto>(
    orgId: string,
    id: string,
    fields: K[],
    opts: { serviceOnly?: boolean; strict?: boolean } = {},
  ): Promise<Pick<UserDto, K> | null> {
    const oid = oidOf(id);
    if (oid === undefined) return null;
    const rows = await this.rowsOf(
      opts.serviceOnly
        ? "/* pipeshub:identity.user_live_service_fields */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text AND NOT u.is_deleted AND u.kind = 'service' ORDER BY u.id"
        : '/* pipeshub:identity.user_live_fields */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text AND NOT u.is_deleted ORDER BY u.id',
      orgId,
      [oid],
    );
    return rows[0] ? pick(rows[0], fields) : null;
  }

  async getByIdOrEmail<K extends keyof UserDto>(
    orgId: string,
    key: { id: string } | { email: string },
    fields: K[],
  ): Promise<Pick<UserDto, K> | null> {
    let rows: UserRow[];
    if ('id' in key) {
      const oid = oidOf(key.id);
      if (oid === undefined) return null;
      rows = await this.rowsOf(
        '/* pipeshub:identity.user_by_id_any */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text ORDER BY u.id',
        orgId,
        [oid],
      );
    } else {
      rows = await this.rowsOf(
        '/* pipeshub:identity.user_by_email_any */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 ORDER BY u.id',
        orgId,
        [stripNul(key.email, 'user', 'email').toLowerCase()],
      );
    }
    return rows[0] ? pick(rows[0], fields) : null;
  }

  async listFieldsByIds<K extends keyof UserDto>(
    ids: string[],
    fields: K[],
  ): Promise<Array<Pick<UserDto, K>>> {
    const list = oidsOf(ids);
    if (list.length === 0) return [];
    const rows = await this.rowsOf(
      '/* pipeshub:identity.user_fields_by_ids */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = ANY($2::text[]) ORDER BY u.id',
      undefined,
      [list],
    );
    return rows.map((r) => pick(r, fields));
  }

  async getOldestId(orgId: string): Promise<string | null> {
    const rows = await this.rowsOf(
      '/* pipeshub:identity.user_oldest */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND NOT u.is_deleted ORDER BY u.created_at, u.id LIMIT 1',
      orgId,
      [],
    );
    return rows[0]?.id ?? null;
  }

  async getAnyById(id: string): Promise<UserDto | null> {
    const oid = oidOf(id);
    return oid === undefined
      ? null
      : this.one(
          '/* pipeshub:identity.user_get_any_by_id */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text LIMIT 1',
          [oid],
          undefined,
        );
  }

  async getLiveByEmail(orgId: string, email: string): Promise<UserDto | null> {
    return this.one(
      '/* pipeshub:identity.user_get_live_by_email */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 AND NOT u.is_deleted LIMIT 1',
      [stripNul(email, 'user', 'email').toLowerCase()],
      orgId,
    );
  }

  async getContact(
    id: string,
  ): Promise<Pick<UserDto, '_id' | 'fullName' | 'email'> | null> {
    const u = await this.getAnyById(id);
    if (!u) return null;
    return {
      _id: u._id,
      email: u.email,
      ...(u.fullName !== undefined && { fullName: u.fullName }),
    };
  }

  // ---- lists
  async listLiveByIds(orgId: string, ids: string[]): Promise<UserDto[]> {
    const list = oidsOf(ids);
    return list.length === 0
      ? []
      : (
          await this.rowsOf(
            '/* pipeshub:identity.user_list_live_by_ids */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND NOT u.is_deleted AND u.id = ANY($2::text[]) ORDER BY u.id',
            orgId,
            [list],
          )
        ).map(dto);
  }

  async listNamesByIds(
    orgId: string,
    ids: string[],
  ): Promise<MemberSummaryDto[]> {
    const list = oidsOf(ids);
    if (list.length === 0) return [];
    return (
      await this.rowsOf(
        '/* pipeshub:identity.user_list_names */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND NOT u.is_deleted AND u.id = ANY($2::text[]) ORDER BY u.id',
        orgId,
        [list],
      )
    ).map((r) => summary(r));
  }

  async listAllLive(orgId: string): Promise<UserDto[]> {
    return (
      await this.rowsOf(
        '/* pipeshub:identity.user_list_all_live */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND NOT u.is_deleted ORDER BY u.id',
        orgId,
        [],
      )
    ).map(dto);
  }

  async listSummariesByIds(
    orgId: string,
    ids: string[],
  ): Promise<
    Array<Pick<UserDto, '_id' | 'hasLoggedIn' | 'fullName' | 'role'>>
  > {
    const list = oidsOf(ids);
    if (list.length === 0) return [];
    const rows = await this.rowsOf(
      '/* pipeshub:identity.user_list_summaries */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = ANY($2::text[]) ORDER BY u.id',
      orgId,
      [list],
    );
    return rows.map(
      (r) =>
        pick(r, ['hasLoggedIn', 'fullName', 'role'] as Array<
          'hasLoggedIn' | 'fullName' | 'role'
        >) as Pick<UserDto, '_id' | 'hasLoggedIn' | 'fullName' | 'role'>,
    );
  }

  async listDirectory(
    orgId: string,
    q: DirectoryQuery,
  ): Promise<{ rows: UserDto[]; total: number }> {
    const blockedIds =
      q.blocked !== undefined
        ? await getIdentityPort().blockedUserIds(orgId)
        : undefined;
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const org = p.add(k.orgId);
      const conds = [`u.org_id = ${org}`, 'NOT u.is_deleted'];
      if (!q.includeServiceAccounts) conds.push("u.kind <> 'service'");
      if (q.includeSlackOnlyAccounts !== true) conds.push("u.kind <> 'slack'");
      if (q.search !== undefined && q.search !== '')
        conds.push(search(p, q.search));
      const loggedIn =
        q.hasLoggedIn !== undefined
          ? `u.has_logged_in = ${p.add(q.hasLoggedIn)}`
          : undefined;
      if (blockedIds !== undefined) {
        const ids = p.add(oidsOf(blockedIds));
        if (q.blocked === true) {
          // blocked people, and with `hasLoggedIn` either condition
          conds.push(
            loggedIn
              ? `(${loggedIn} OR u.id = ANY(${ids}::text[]))`
              : `u.id = ANY(${ids}::text[])`,
          );
        } else {
          if (loggedIn) conds.push(loggedIn);
          conds.push(`NOT (u.id = ANY(${ids}::text[]))`);
        }
      } else if (loggedIn) conds.push(loggedIn);
      if (q.groupIds !== undefined && q.groupIds.length > 0) {
        conds.push(`u.id IN (SELECT m.user_id FROM identity.user_group_members m JOIN identity.user_groups g ON g.org_id = m.org_id AND g.id = m.group_id
          WHERE m.org_id = ${org} AND g.org_id = ${org} AND NOT g.is_deleted AND g.id = ANY(${p.add(oidsOf(q.groupIds))}::text[]))`);
      }
      const where = conds.join(' AND ');
      const page = await k.query<UserRow>(
        `/* pipeshub:identity.user_directory */ SELECT ${COLS} FROM identity.users u WHERE ${where}
          ORDER BY u.full_name COLLATE "C" ASC NULLS FIRST, u.id LIMIT ${p.add(q.limit)} OFFSET ${p.add(q.skip)}`,
        p.values,
      );
      const count = await k.query<{ n: string }>(
        `/* pipeshub:identity.user_directory_count */ SELECT count(*) AS n FROM identity.users u WHERE ${where}`,
        p.values.slice(0, p.values.length - 2),
      );
      return { rows: page.rows.map(dto), total: Number(count.rows[0]?.n ?? 0) };
    });
  }

  async listWithGroups(
    orgId: string,
    opts: { includeServiceAccounts?: boolean } = {},
  ): Promise<UserWithGroupsDto[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{
        id: string;
        full_name: string | null;
        has_logged_in: boolean;
        groups: Array<{ name: string; type: string }>;
      }>(
        `/* pipeshub:identity.user_list_with_groups */ SELECT u.id, u.full_name, u.has_logged_in,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('name', g.name, 'type', g.type) ORDER BY g.id)
                        FROM identity.user_group_members m JOIN identity.user_groups g ON g.org_id = m.org_id AND g.id = m.group_id
                       WHERE m.org_id = $1 AND g.org_id = $1 AND m.user_id = u.id AND NOT g.is_deleted), '[]'::jsonb) AS groups
           FROM identity.users u WHERE u.org_id = $1 AND NOT u.is_deleted AND u.kind <> 'slack' AND ($2::boolean OR u.kind <> 'service') ORDER BY u.id`,
        [k.orgId, opts.includeServiceAccounts === true],
      );
      return rows.map((r) => ({
        _id: r.id,
        orgId: k.orgId,
        ...(r.full_name !== null && { fullName: r.full_name }),
        hasLoggedIn: r.has_logged_in,
        groups: r.groups,
      }));
    });
  }

  async listMembersPage(
    orgId: string,
    ids: string[],
    q: {
      search?: string;
      skip: number;
      limit: number;
      includeServiceAccounts?: boolean;
    },
  ): Promise<{ rows: MemberSummaryDto[]; total: number }> {
    const list = oidsOf(ids);
    if (list.length === 0) return { rows: [], total: 0 };
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const conds = [
        `u.org_id = ${p.add(k.orgId)}`,
        'NOT u.is_deleted',
        `u.id = ANY(${p.add(list)}::text[])`,
      ];
      if (q.includeServiceAccounts !== true) conds.push("u.kind <> 'service'");
      if (q.search !== undefined && q.search !== '')
        conds.push(search(p, q.search));
      const where = conds.join(' AND ');
      const count = await k.query<{ n: string }>(
        `/* pipeshub:identity.user_members_count */ SELECT count(*) AS n FROM identity.users u WHERE ${where}`,
        p.values,
      );
      const page = await k.query<UserRow>(
        `/* pipeshub:identity.user_members_page */ SELECT ${COLS} FROM identity.users u WHERE ${where} ORDER BY u.id LIMIT ${p.add(q.limit)} OFFSET ${p.add(q.skip)}`,
        p.values,
      );
      return {
        rows: page.rows.map((r) => summary(r)),
        total: Number(count.rows[0]?.n ?? 0),
      };
    });
  }

  async listByEmails(orgId: string, emails: string[]): Promise<UserDto[]> {
    if (emails.length === 0) return [];
    return (
      await this.rowsOf(
        '/* pipeshub:identity.user_list_by_emails */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = ANY($2::text[]) ORDER BY u.id',
        orgId,
        [emails.map((e) => stripNul(e, 'user', 'email').toLowerCase())],
      )
    ).map(dto);
  }

  async listAdminIds(orgId: string): Promise<string[]> {
    return (
      await this.rowsOf(
        "/* pipeshub:identity.user_list_admin_ids */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.role = 'admin' AND NOT u.is_deleted AND u.kind <> 'service' ORDER BY u.id",
        orgId,
        [],
      )
    ).map((r) => r.id);
  }

  async listServiceAccounts(orgId: string): Promise<UserDto[]> {
    return (
      await this.rowsOf(
        "/* pipeshub:identity.user_list_service */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.kind = 'service' AND NOT u.is_deleted ORDER BY u.created_at DESC, u.id DESC",
        orgId,
        [],
      )
    ).map(dto);
  }

  async serviceAccountIdsAmong(
    orgId: string,
    ids: string[],
  ): Promise<string[]> {
    const list = oidsOf(ids);
    if (list.length === 0) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ id: string }>(
        "/* pipeshub:identity.user_service_among */ SELECT u.id FROM identity.users u WHERE u.org_id = $1 AND u.id = ANY($2::text[]) AND u.kind = 'service' ORDER BY u.id",
        [k.orgId, list],
      );
      return rows.map((r) => r.id);
    });
  }

  async getServiceAccount(orgId: string, id: string): Promise<UserDto | null> {
    const oid = oidOf(id);
    return oid === undefined
      ? null
      : this.one(
          "/* pipeshub:identity.user_get_service */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text AND u.kind = 'service' AND NOT u.is_deleted LIMIT 1",
          [oid],
          orgId,
        );
  }

  async countActiveAdmins(orgId: string, tx?: Tx): Promise<number> {
    return inUnit(tx, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ n: string }>(
        "/* pipeshub:identity.user_count_admins */ SELECT count(*) AS n FROM identity.users u WHERE u.org_id = $1 AND u.role = 'admin' AND NOT u.is_deleted AND u.kind <> 'service'",
        [k.orgId],
      );
      return Number(rows[0]?.n ?? 0);
    });
  }

  // ---- address checks
  async existsLiveEmail(email: string): Promise<boolean> {
    return (await this.getLiveByEmailAmbient(email)) !== null;
  }

  private getLiveByEmailAmbient(email: string): Promise<UserDto | null> {
    return this.one(
      '/* pipeshub:identity.user_exists_live_email */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 AND NOT u.is_deleted LIMIT 1',
      [stripNul(email, 'user', 'email').toLowerCase()],
      undefined,
    );
  }

  async emailTakenByOther(
    orgId: string,
    email: string,
    exceptId: string,
  ): Promise<boolean> {
    const rows = await this.rowsOf(
      '/* pipeshub:identity.user_email_taken_by_other */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 AND u.id <> $3::text AND NOT u.is_deleted ORDER BY u.id',
      orgId,
      [stripNul(email, 'user', 'email').toLowerCase(), oidOf(exceptId) ?? ''],
    );
    return rows.length > 0;
  }

  async emailTakenElsewhere(email: string, exceptId: string): Promise<boolean> {
    const address = stripNul(email, 'user', 'email').toLowerCase();
    // in this organisation, removed accounts included (the install has one)
    const here = await this.rowsOf(
      '/* pipeshub:identity.user_email_taken_elsewhere */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 AND u.id <> $3::text ORDER BY u.id',
      undefined,
      [address, oidOf(exceptId) ?? ''],
    );
    return here.length > 0;
  }

  async listSignInEligible(
    orgId: string,
  ): Promise<Array<Pick<UserDto, '_id' | 'fullName' | 'email'>>> {
    const rows = await this.rowsOf(
      `/* pipeshub:identity.user_sign_in_eligible */ SELECT @{COLS} FROM identity.users u
        WHERE u.org_id = $1 AND NOT u.is_deleted AND u.has_logged_in AND NOT u.is_disabled AND u.kind <> 'service' ORDER BY u.id`,
      orgId,
      [],
    );
    return rows.map((r) => pick(r, ['_id', 'fullName', 'email']));
  }

  async listLiveByEmail<K extends keyof UserDto>(
    email: string,
    fields: K[],
    orgId?: string,
  ): Promise<Array<Pick<UserDto, K>>> {
    const rows = await this.rowsOf(
      '/* pipeshub:identity.user_live_by_email */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 AND NOT u.is_deleted ORDER BY u.id',
      orgId,
      [stripNul(email, 'user', 'email').toLowerCase()],
    );
    return rows.map((r) => pick(r, fields));
  }

  async findActiveByEmail(
    email: string,
  ): Promise<{ _id: string; orgId: string; email: string } | null> {
    const rows = await this.rowsOf(
      '/* pipeshub:identity.user_active_by_email */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 AND NOT u.is_deleted ORDER BY u.id LIMIT 1',
      undefined,
      [stripNul(email, 'user', 'email').toLowerCase()],
    );
    const r = rows[0];
    return r ? { _id: r.id, orgId: r.org_id, email: r.email } : null;
  }

  async listLiveMembershipsByEmail(
    email: string,
  ): Promise<Array<{ _id: string; orgId: string }>> {
    const rows = await this.rowsOf(
      '/* pipeshub:identity.user_memberships_by_email */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 AND NOT u.is_deleted ORDER BY u.id',
      undefined,
      [stripNul(email, 'user', 'email').toLowerCase()],
    );
    return rows.map((r) => ({ _id: r.id, orgId: r.org_id }));
  }

  async listLiveFieldsByIds<K extends keyof UserDto>(
    ids: string[],
    fields: K[],
  ): Promise<Array<Pick<UserDto, K>>> {
    const list = oidsOf(ids);
    if (list.length === 0) return [];
    const rows = await this.rowsOf(
      '/* pipeshub:identity.user_live_fields_by_ids */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = ANY($2::text[]) AND NOT u.is_deleted ORDER BY u.id',
      undefined,
      [list],
    );
    return rows.map((r) => pick(r, fields));
  }

  async hasRemovedAccount(orgId: string, email: string): Promise<boolean> {
    return (
      (
        await this.rowsOf(
          '/* pipeshub:identity.user_has_removed */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 AND u.is_deleted ORDER BY u.id',
          orgId,
          [stripNul(email, 'user', 'email').toLowerCase()],
        )
      ).length > 0
    );
  }

  // ---- writes
  async create(orgId: string, fields: UserFields): Promise<UserDto> {
    return inUnit(undefined, { write: true }, (k) => {
      assertOrg(k, orgId);
      return insertUser(k, fields, new Date());
    });
  }

  async createMany(orgId: string, rows: UserFields[]): Promise<UserDto[]> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const now = new Date();
      const out: UserDto[] = [];
      for (const r of rows) out.push(await insertUser(k, r, now));
      return out;
    });
  }

  async updateProfile(
    orgId: string,
    id: string,
    fields: UserFields,
    tx?: Tx,
  ): Promise<UserDto | null> {
    const oid = oidOf(id);
    if (oid === undefined) return null;
    return inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const cur = await k.query<UserRow>(
        `/* pipeshub:identity.user_lock_live */ SELECT ${COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text AND NOT u.is_deleted FOR UPDATE`,
        [k.orgId, oid],
      );
      if (!cur.rows[0]) return null;
      const cols = writable(fields);
      const next = (f: string, dflt: unknown): unknown =>
        cols.some(([n]) => n === f) ? cols.find(([n]) => n === f)?.[2] : dflt;
      rule(
        next('kind', cur.rows[0].kind),
        next('role', cur.rows[0].role),
        next('email', cur.rows[0].email),
      );
      if (cols.length === 0) return dto(cur.rows[0]);
      const sets = cols.map(
        ([, c], i) =>
          `${c} = $${String(i + 3)}${c === 'address' ? '::jsonb' : ''}`,
      );
      const { rows } = await k.query<UserRow>(
        `/* pipeshub:identity.user_update */ UPDATE identity.users u SET ${sets.join(', ')}, updated_at = $${String(cols.length + 3)} WHERE u.org_id = $1 AND u.id = $2::text RETURNING ${COLS}`,
        [k.orgId, oid, ...cols.map(([, , v]) => v), new Date()],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  async markDeleted(
    orgId: string,
    id: string,
    extra?: { signedOut?: boolean; deletedBy?: string },
  ): Promise<UserDto | null> {
    const oid = oidOf(id);
    if (oid === undefined) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const sets = ['is_deleted = true', 'updated_at = $3'];
      const values: unknown[] = [k.orgId, oid, new Date()];
      if (extra?.signedOut) sets.push('has_logged_in = false');
      if (extra && 'deletedBy' in extra) {
        values.push(
          extra.deletedBy === undefined
            ? null
            : stripNul(extra.deletedBy, 'user', 'deletedBy'),
        );
        sets.push(`deleted_by = $${String(values.length)}`);
      }
      const { rows } = await k.query<UserRow>(
        `/* pipeshub:identity.user_mark_deleted */ UPDATE identity.users u SET ${sets.join(', ')} WHERE u.org_id = $1 AND u.id = $2::text RETURNING ${COLS}`,
        values,
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  async upgradeSlackOnly(orgId: string, id: string): Promise<boolean> {
    const oid = oidOf(id);
    if (oid === undefined) return false;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rowCount } = await k.query(
        "/* pipeshub:identity.user_upgrade_slack_only */ UPDATE identity.users u SET kind = 'human', updated_at = $3 WHERE u.org_id = $1 AND u.id = $2::text AND u.kind = 'slack'",
        [k.orgId, oid, new Date()],
      );
      return rowCount === 1;
    });
  }

  async restoreByEmails(orgId: string, emails: string[]): Promise<UserDto[]> {
    if (emails.length === 0) return [];
    const list = emails.map((e) => stripNul(e, 'user', 'email').toLowerCase());
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        '/* pipeshub:identity.user_restore */ UPDATE identity.users u SET is_deleted = false, updated_at = $3 WHERE u.org_id = $1 AND u.email = ANY($2::text[]) AND u.is_deleted',
        [k.orgId, list, new Date()],
      );
      const { rows } = await k.query<UserRow>(
        `/* pipeshub:identity.user_restored */ SELECT ${COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = ANY($2::text[]) AND NOT u.is_deleted ORDER BY u.id`,
        [k.orgId, list],
      );
      return rows.map(dto);
    });
  }

  async promoteToAdmin(orgId: string, ids: string[]): Promise<void> {
    const list = oidsOf(ids);
    if (list.length === 0) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const barred = await k.query<{ kind: UserKind }>(
        "/* pipeshub:identity.user_promote_check */ SELECT u.kind FROM identity.users u WHERE u.org_id = $1 AND u.id = ANY($2::text[]) AND u.kind IN ('service', 'slack') ORDER BY u.kind LIMIT 1",
        [k.orgId, list],
      );
      if (barred.rows[0])
        assertServiceAccountRole(barred.rows[0].kind, 'admin');
      await k.query(
        "/* pipeshub:identity.user_promote */ UPDATE identity.users u SET role = 'admin', updated_at = $3 WHERE u.org_id = $1 AND u.id = ANY($2::text[])",
        [k.orgId, list, new Date()],
      );
    });
  }

  async setRole(orgId: string, id: string, role: UserRole): Promise<void> {
    const oid = oidOf(id);
    if (oid === undefined) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      if (role === 'admin') {
        const barred = await k.query<{ kind: UserKind }>(
          "/* pipeshub:identity.user_role_check */ SELECT u.kind FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text AND u.kind IN ('service', 'slack')",
          [k.orgId, oid],
        );
        if (barred.rows[0])
          assertServiceAccountRole(barred.rows[0].kind, 'admin');
      }
      await k.query(
        '/* pipeshub:identity.user_set_role */ UPDATE identity.users u SET role = $3, updated_at = $4 WHERE u.org_id = $1 AND u.id = $2::text AND NOT u.is_deleted',
        [k.orgId, oid, role, new Date()],
      );
    });
  }

  async lockOrgAdminChanges(orgId: string, tx: Tx): Promise<void> {
    await inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        '/* pipeshub:identity.org_lock_admin_changes */ SELECT 1 FROM identity.orgs o WHERE o.org_id = $1 AND NOT o.is_deleted FOR UPDATE',
        [k.orgId],
      );
    });
  }

  async deleteById(id: string): Promise<void> {
    const oid = oidOf(id);
    if (oid === undefined) return;
    await inUnit(undefined, { write: true }, (k) =>
      k.query(
        '/* pipeshub:identity.user_delete */ DELETE FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text',
        [k.orgId, oid],
      ),
    );
  }

  /**
   * Deletes a removed account for good. Its credential row goes with it (the foreign key cascades).
   */
  async deleteRemoved(orgId: string, id: string): Promise<boolean> {
    const oid = oidOf(id);
    if (oid === undefined) return false;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const res = await k.query(
        '/* pipeshub:identity.user_delete_removed */ DELETE FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text AND u.is_deleted',
        [k.orgId, oid],
      );
      return (res.rowCount ?? 0) > 0;
    });
  }

  async listRemovedIdsByEmail(orgId: string, email: string): Promise<string[]> {
    return (
      await this.rowsOf(
        '/* pipeshub:identity.user_list_removed_ids */ SELECT @{COLS} FROM identity.users u WHERE u.org_id = $1 AND u.email = $2 AND u.is_deleted ORDER BY u.id',
        orgId,
        [stripNul(email, 'user', 'email').toLowerCase()],
      )
    ).map((r) => r.id);
  }

  async setDisabledByEmailDomain(
    orgId: string,
    domain: string,
    exceptId: string | undefined,
    disabled: boolean,
  ): Promise<number> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const except = exceptId === undefined ? undefined : oidOf(exceptId);
      const { rows } = await k.query(
        `/* pipeshub:identity.user_set_disabled_by_domain */ UPDATE identity.users u SET is_disabled = $3, updated_at = $4
          WHERE u.org_id = $1 AND NOT u.is_deleted AND u.email LIKE $2 ESCAPE '\\' AND ($5::text IS NULL OR u.id <> $5::text) RETURNING u.id`,
        [
          k.orgId,
          `%@${likeEscape(stripNul(domain, 'user', 'domain').toLowerCase())}`,
          disabled,
          new Date(),
          except ?? null,
        ],
      );
      return rows.length;
    });
  }

  async changeEmail(id: string, email: string): Promise<UserDto | null> {
    return this.moveAddress(id, email);
  }

  async correctEmail(id: string, email: string): Promise<void> {
    await this.moveAddress(id, email);
  }

  /**
   * Moves the account's address. A taken address is the unique key of (organisation, address) refusing the update.
   */
  private async moveAddress(
    id: string,
    email: string,
  ): Promise<UserDto | null> {
    const oid = oidOf(id);
    if (oid === undefined) return null;
    const next = stripNul(email, 'user', 'email').toLowerCase();
    return inUnit(undefined, { write: true }, async (k) => {
      const cur = await k.query<UserRow>(
        `/* pipeshub:identity.user_lock */ SELECT ${COLS} FROM identity.users u WHERE u.org_id = $1 AND u.id = $2::text FOR UPDATE`,
        [k.orgId, oid],
      );
      const row = cur.rows[0];
      if (!row) return null;
      rule(row.kind, row.role, next);
      const { rows } = await k.query<UserRow>(
        `/* pipeshub:identity.user_set_email */ UPDATE identity.users u SET email = $3, updated_at = $4 WHERE u.org_id = $1 AND u.id = $2::text RETURNING ${COLS}`,
        [k.orgId, oid, next, new Date()],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  // ---- service accounts
  async restoreServiceAccount(
    orgId: string,
    id: string,
    input: RestoreServiceAccountInput,
  ): Promise<UserDto | null> {
    const oid = oidOf(id);
    if (oid === undefined) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      assertServiceAccountRole('service', input.role);
      const { rows } = await k.query<UserRow>(
        `/* pipeshub:identity.service_restore */ UPDATE identity.users u SET full_name = $3, role = $4, is_disabled = true, is_deleted = false, restore_op_id = $5,
            description = $6, deleted_by = NULL, updated_at = $7
          WHERE u.org_id = $1 AND u.id = $2::text AND u.kind = 'service' AND u.is_deleted RETURNING ${COLS}`,
        [
          k.orgId,
          oid,
          stripNul(input.fullName, 'user', 'fullName'),
          input.role,
          input.restoreOpId,
          input.description === undefined
            ? null
            : stripNul(input.description, 'user', 'description'),
          new Date(),
        ],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  async abortServiceAccountRestore(
    id: string,
    restoreOpId: string,
  ): Promise<void> {
    const oid = oidOf(id);
    if (oid === undefined) return;
    await inUnit(undefined, { write: true }, (k) =>
      k.query(
        '/* pipeshub:identity.service_restore_abort */ UPDATE identity.users u SET is_deleted = true, restore_op_id = NULL, updated_at = $4 WHERE u.org_id = $1 AND u.id = $2::text AND u.restore_op_id = $3',
        [k.orgId, oid, restoreOpId, new Date()],
      ),
    );
  }

  async finishServiceAccountRestore(
    id: string,
    restoreOpId: string,
  ): Promise<UserDto | null> {
    const oid = oidOf(id);
    if (oid === undefined) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      const { rows } = await k.query<UserRow>(
        `/* pipeshub:identity.service_restore_finish */ UPDATE identity.users u SET is_disabled = false, restore_op_id = NULL, updated_at = $4
          WHERE u.org_id = $1 AND u.id = $2::text AND u.restore_op_id = $3 AND NOT u.is_deleted RETURNING ${COLS}`,
        [k.orgId, oid, restoreOpId, new Date()],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  async enableServiceAccount(orgId: string, id: string): Promise<boolean> {
    const oid = oidOf(id);
    if (oid === undefined) return false;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query(
        "/* pipeshub:identity.service_enable */ UPDATE identity.users u SET is_disabled = false, updated_at = $3 WHERE u.org_id = $1 AND u.id = $2::text AND u.kind = 'service' AND NOT u.is_deleted AND u.restore_op_id IS NULL RETURNING u.id",
        [k.orgId, oid, new Date()],
      );
      return rows.length > 0;
    });
  }

  async updateServiceAccount(
    orgId: string,
    id: string,
    changes: Record<string, unknown>,
  ): Promise<boolean> {
    const oid = oidOf(id);
    if (oid === undefined) return false;
    const cols = writable(changes);
    if (cols.length === 0) return false;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const sets = cols.map(
        ([, c], i) =>
          `${c} = $${String(i + 3)}${c === 'address' ? '::jsonb' : ''}`,
      );
      const res = await k.query(
        `/* pipeshub:identity.service_update */ UPDATE identity.users u SET ${sets.join(', ')}, updated_at = $${String(cols.length + 3)}
          WHERE u.org_id = $1 AND u.id = $2::text AND u.kind = 'service' AND NOT u.is_deleted`,
        [k.orgId, oid, ...cols.map(([, , v]) => v), new Date()],
      );
      return (res.rowCount ?? 0) > 0;
    });
  }
}

function summary(r: UserRow): MemberSummaryDto {
  return {
    _id: r.id,
    ...(r.full_name !== null && { fullName: r.full_name }),
    ...(r.first_name !== null && { firstName: r.first_name }),
    ...(r.last_name !== null && { lastName: r.last_name }),
    email: r.email,
  };
}
