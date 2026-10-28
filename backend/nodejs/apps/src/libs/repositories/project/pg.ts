/**
 * PostgreSQL implementation of the project repository (`DOC_STORE=postgres`). A call that names no org (a lookup by project id)
 * runs for the org of the unit it joins, so a project of another org is not found. Members are rows in their own table with a
 * position that keeps the order they were added in; a project that gains or loses a member takes a new `__v`, as the stored
 * array did. Writers lock the project row first, so two changes to one project's members queue instead of losing one.
 */
import { stripNul } from '../nul';
import {
  Params,
  assertOrg,
  fill,
  inUnit,
  likeEscape,
  newObjectId,
  oidOf,
} from '../pg-util';
import type { Tx as PgKernelTx } from '../../db';
import type { Tx } from '../uow';
import type {
  IProjectAppliedFilters,
  IProjectKnowledgeScope,
} from '../../../modules/projects/types/project.interfaces';
import type {
  NewProject,
  ProjectDto,
  ProjectListQuery,
  ProjectMemberInput,
  ProjectPatch,
  ProjectRepository,
} from './types';

interface Row {
  id: string;
  org_id: string;
  user_id: string;
  name: string;
  description: string | null;
  icon: string | null;
  color: string | null;
  instructions: string | null;
  knowledge_scope: IProjectKnowledgeScope;
  applied_filters: IProjectAppliedFilters;
  tools: string[];
  linked_knowledge_base_id: string | null;
  visibility: 'private' | 'org';
  chat_sharing: 'private' | 'members';
  is_pinned: boolean;
  pinned_json: string[];
  is_archived: boolean;
  archived_by: string | null;
  is_deleted: boolean;
  deleted_by: string | null;
  last_activity_at: Date;
  metadata: Record<string, unknown> | null;
  version: number;
  created_at: Date;
  updated_at: Date;
  members: Array<{
    principalType: 'user' | 'team';
    principalId: string;
    role: 'viewer' | 'editor';
    addedBy: string;
    addedAt: string;
  }>;
}

const COLUMNS = `p.*, to_jsonb(p.pinned_by) AS pinned_json, COALESCE((SELECT jsonb_agg(jsonb_build_object('principalType', m.principal_type, 'principalId', m.principal_id::text,
    'role', m.role, 'addedBy', m.added_by::text, 'addedAt', m.added_at) ORDER BY m.pos)
    FROM content.project_members m WHERE m.org_id = $1 AND m.project_id = p.id), '[]'::jsonb) AS members FROM content.projects p`;

const GROUPS = ['apps', 'kb', 'recordGroups', 'records'] as const;

/** The stored scope always has its four lists, as the document schema gave it. */
function shape(
  v: object | undefined,
  node?: (x: never) => unknown,
): Record<string, unknown[]> {
  const out: Record<string, unknown[]> = {};
  for (const g of GROUPS) {
    const list =
      (v as Record<string, unknown[] | undefined> | undefined)?.[g] ?? [];
    out[g] = node ? list.map((x) => node(x as never)) : list;
  }
  return out;
}
const filterNode = (n: {
  id: string;
  name: string;
  nodeType: string;
  connector?: string;
}): Record<string, unknown> => ({ ...n, connector: n.connector ?? '' });

function dto(r: Row): ProjectDto {
  const o: Record<string, unknown> = {
    _id: r.id,
    orgId: r.org_id,
    userId: r.user_id,
    name: r.name,
    knowledgeScope: shape(r.knowledge_scope),
    appliedFilters: shape(r.applied_filters),
    tools: r.tools,
    linkedKnowledgeBaseId: r.linked_knowledge_base_id,
    visibility: r.visibility,
    chatSharing: r.chat_sharing,
    members: r.members.map((m) => ({ ...m, addedAt: new Date(m.addedAt) })),
    isPinned: r.is_pinned,
    pinnedBy: r.pinned_json,
    isArchived: r.is_archived,
    isDeleted: r.is_deleted,
    lastActivityAt: r.last_activity_at.getTime(),
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    __v: r.version,
  };
  const optional: Array<[string, unknown]> = [
    ['description', r.description],
    ['icon', r.icon],
    ['color', r.color],
    ['instructions', r.instructions],
    ['archivedBy', r.archived_by],
    ['deletedBy', r.deleted_by],
    ['metadata', r.metadata],
  ];
  for (const [key, v] of optional) if (v !== null) o[key] = v;
  return o as unknown as ProjectDto;
}

async function load(
  k: PgKernelTx,
  id: string,
  state: 'live' | 'deleted' | 'any' = 'any',
): Promise<ProjectDto | null> {
  const { rows } = await k.query<Row>(
    fill(
      `/* pipeshub:content.project_get */ SELECT @{COLUMNS} WHERE p.org_id = $1 AND p.id = $2@{B}`,
      {
        COLUMNS,
        B:
          state === 'any'
            ? ''
            : state === 'live'
              ? ' AND NOT p.is_deleted'
              : ' AND p.is_deleted',
      },
    ),
    [k.orgId, id],
  );
  return rows[0] ? dto(rows[0]) : null;
}

/** Locks the live project's row; false when there is none. */
async function lockLive(k: PgKernelTx, id: string): Promise<Row | undefined> {
  const { rows } = await k.query<Row>(
    '/* pipeshub:content.project_lock */ SELECT * FROM content.projects WHERE org_id = $1 AND id = $2 AND NOT is_deleted FOR UPDATE',
    [k.orgId, id],
  );
  return rows[0];
}

const touchedAt = (): Date => new Date();

export class PgProjectRepository implements ProjectRepository {
  async getLive(id: string): Promise<ProjectDto | null> {
    return this.byId(id, 'live');
  }

  async getDeleted(id: string): Promise<ProjectDto | null> {
    return this.byId(id, 'deleted');
  }

  private async byId(
    id: string,
    state: 'live' | 'deleted',
  ): Promise<ProjectDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: false }, (k) => load(k, oid, state));
  }

  async create(
    orgId: string,
    userId: string,
    input: NewProject,
  ): Promise<ProjectDto> {
    const owner = oidOf(userId);
    if (!owner)
      throw new Error('the owner of a project must be a valid user id');
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const i = stripNul(input, 'project', 'row');
      const id = newObjectId();
      const now = touchedAt();
      await k.query(
        `/* pipeshub:content.project_create */ INSERT INTO content.projects
           (org_id, id, user_id, name, description, icon, color, instructions, knowledge_scope, applied_filters, tools, last_activity_at, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11, $12, $12, $12)`,
        [
          k.orgId,
          id,
          owner,
          i.name.trim(),
          i.description ?? null,
          i.icon ?? null,
          i.color ?? null,
          i.instructions ?? null,
          JSON.stringify(shape(i.knowledgeScope)),
          JSON.stringify(shape(i.appliedFilters, filterNode)),
          i.tools,
          now,
        ],
      );
      return (await load(k, id)) as ProjectDto;
    });
  }

  async list(
    orgId: string,
    userId: string,
    q: ProjectListQuery,
  ): Promise<{ rows: ProjectDto[]; total: number }> {
    const me = oidOf(userId);
    if (!me) return { rows: [], total: 0 };
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      p.add(k.orgId); // $1: the org, also the one the members subquery uses
      const user = p.add(me);
      const member = `EXISTS (SELECT 1 FROM content.project_members m WHERE m.org_id = $1 AND m.project_id = p.id`;
      const mine = `p.user_id = ${user}::text`;
      const asUser = `${member} AND m.principal_type = 'user' AND m.principal_id = ${user}::text)`;
      const asTeam = (): string =>
        `${member} AND m.principal_type = 'team' AND m.principal_id = ANY(${p.add(q.teamIds)}::text[]))`;
      const scope =
        q.scope === 'mine'
          ? [mine]
          : q.scope === 'shared'
            ? [asUser, asTeam()]
            : [mine, asUser, asTeam(), "p.visibility = 'org'"];
      const where = [
        'p.org_id = $1',
        'NOT p.is_deleted',
        `(${scope.join(' OR ')})`,
      ];
      if (q.isArchived !== undefined)
        where.push(`p.is_archived = ${p.add(q.isArchived)}`);
      else if (!q.includeArchived) where.push('NOT p.is_archived');
      if (q.search)
        where.push(`p.name ILIKE ${p.add(`%${likeEscape(q.search)}%`)}`);
      const filter = where.join(' AND ');
      const total = await k.query<{ n: number }>(
        fill(
          `/* pipeshub:content.project_count */ SELECT count(*)::int AS n FROM content.projects p WHERE @{A}`,
          { A: filter },
        ),
        p.values,
      );
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.project_list */ SELECT @{COLUMNS} WHERE @{B} ORDER BY (${user}::text = ANY(p.pinned_by) OR (p.is_pinned AND p.user_id = ${user}::text)) DESC, p.last_activity_at DESC, p.id DESC
          LIMIT @{C} OFFSET @{D}`,
          { COLUMNS, B: filter, C: p.add(q.limit), D: p.add(q.skip) },
        ),
        p.values,
      );
      return { rows: rows.map(dto), total: total.rows[0]?.n ?? 0 };
    });
  }

  async update(id: string, patch: ProjectPatch): Promise<ProjectDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      if (!(await lockLive(k, oid))) return null;
      const p = new Params();
      const sets: string[] = [];
      for (const [key, v] of Object.entries(
        stripNul(patch, 'project', 'patch'),
      )) {
        switch (key) {
          case 'name':
            sets.push(`name = ${p.add(v ?? '')}`);
            break;
          case 'description':
            sets.push(`description = ${p.add(v ?? null)}`);
            break;
          case 'icon':
            sets.push(`icon = ${p.add(v ?? null)}`);
            break;
          case 'color':
            sets.push(`color = ${p.add(v ?? null)}`);
            break;
          case 'instructions':
            sets.push(`instructions = ${p.add(v ?? null)}`);
            break;
          case 'knowledgeScope':
            sets.push(
              `knowledge_scope = ${p.add(JSON.stringify(shape(v as IProjectKnowledgeScope | undefined)))}::jsonb`,
            );
            break;
          case 'appliedFilters':
            sets.push(
              `applied_filters = ${p.add(JSON.stringify(shape(v as IProjectAppliedFilters | undefined, filterNode)))}::jsonb`,
            );
            break;
          case 'tools':
            sets.push(`tools = ${p.add(v ?? [])}`);
            break;
          case 'visibility':
            sets.push(`visibility = ${p.add(v ?? 'private')}`);
            break;
          case 'chatSharing':
            sets.push(`chat_sharing = ${p.add(v ?? 'private')}`);
            break;
          case 'isArchived':
            sets.push(`is_archived = ${p.add(v ?? false)}`);
            break;
          case 'archivedBy':
            sets.push(
              `archived_by = ${p.add(v === undefined ? null : oidOf(v))}`,
            );
            break;
          default:
            throw new Error(
              `project field '${key}' cannot be written by this store`,
            );
        }
      }
      sets.push(`updated_at = ${p.add(touchedAt())}`);
      await k.query(
        fill(
          `/* pipeshub:content.project_update */ UPDATE content.projects SET @{A} WHERE org_id = @{B} AND id = @{C}`,
          { A: sets.join(', '), B: p.add(k.orgId), C: p.add(oid) },
        ),
        p.values,
      );
      return load(k, oid);
    });
  }

  async markDeleted(
    id: string,
    deletedBy: string,
    tx?: Tx,
  ): Promise<ProjectDto | null> {
    const oid = oidOf(id);
    const by = oidOf(deletedBy);
    if (!oid || !by) return null;
    return inUnit(tx, { write: true }, async (k) => {
      if (!(await lockLive(k, oid))) return null;
      await k.query(
        '/* pipeshub:content.project_mark_deleted */ UPDATE content.projects SET is_deleted = true, deleted_by = $3, updated_at = $4 WHERE org_id = $1 AND id = $2',
        [k.orgId, oid, by, touchedAt()],
      );
      return load(k, oid);
    });
  }

  async upsertMembers(
    id: string,
    members: ProjectMemberInput[],
    addedBy: string,
  ): Promise<ProjectDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      const project = await lockLive(k, oid);
      if (!project) return null;
      const by = oidOf(addedBy);
      let added = 0;
      for (const m of members) {
        // a user is a 24-hex id; a team is a graph id kept as given
        const who =
          m.principalType === 'team'
            ? stripNul(m.principalId, 'project', 'member')
            : oidOf(m.principalId);
        // the owner is implicit and never a member row
        if (
          !who ||
          !by ||
          (m.principalType === 'user' && who === project.user_id)
        )
          continue;
        const { rows } = await k.query<{ inserted: boolean }>(
          `/* pipeshub:content.project_member_upsert */ INSERT INTO content.project_members (org_id, project_id, principal_type, principal_id, role, added_by, added_at, pos)
             VALUES ($1, $2::text, $3, $4::text, $5, $6::text, $7, (SELECT COALESCE(max(pos) + 1, 0) FROM content.project_members WHERE org_id = $1 AND project_id = $2::text))
             ON CONFLICT (org_id, project_id, principal_type, principal_id) DO UPDATE SET role = EXCLUDED.role
             RETURNING (xmax = 0) AS inserted`,
          [k.orgId, oid, m.principalType, who, m.role, by, touchedAt()],
        );
        if (rows[0]?.inserted) added += 1;
      }
      await k.query(
        '/* pipeshub:content.project_touch_members */ UPDATE content.projects SET version = version + $3, updated_at = $4 WHERE org_id = $1 AND id = $2',
        [k.orgId, oid, added > 0 ? 1 : 0, touchedAt()],
      );
      return load(k, oid);
    });
  }

  async removeMember(
    id: string,
    principalId: string,
    principalType: 'user' | 'team',
  ): Promise<ProjectDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      if (!(await lockLive(k, oid))) return null;
      const who =
        principalType === 'team'
          ? stripNul(principalId, 'project', 'member')
          : oidOf(principalId);
      const gone = who
        ? await k.query(
            '/* pipeshub:content.project_member_remove */ DELETE FROM content.project_members WHERE org_id = $1 AND project_id = $2 AND principal_type = $3 AND principal_id = $4',
            [k.orgId, oid, principalType, who],
          )
        : { rowCount: 0 };
      await k.query(
        '/* pipeshub:content.project_touch_members */ UPDATE content.projects SET version = version + $3, updated_at = $4 WHERE org_id = $1 AND id = $2',
        [k.orgId, oid, gone.rowCount ? 1 : 0, touchedAt()],
      );
      return load(k, oid);
    });
  }

  async listWithLinkedKbForMember(
    orgId: string,
    userId: string,
  ): Promise<ProjectDto[]> {
    const me = oidOf(userId);
    if (!me) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.project_linked_for_member */ SELECT @{COLUMNS} WHERE p.org_id = $1 AND NOT p.is_deleted AND p.linked_knowledge_base_id IS NOT NULL
           AND EXISTS (SELECT 1 FROM content.project_members m WHERE m.org_id = $1 AND m.project_id = p.id AND m.principal_type = 'user' AND m.principal_id = $2::text)
           ORDER BY p.id`,
          { COLUMNS },
        ),
        [k.orgId, me],
      );
      return rows.map(dto);
    });
  }

  async removeUserFromAll(orgId: string, userId: string): Promise<void> {
    const me = oidOf(userId);
    if (!me) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        "/* pipeshub:content.project_remove_user */ DELETE FROM content.project_members WHERE org_id = $1 AND principal_type = 'user' AND principal_id = $2",
        [k.orgId, me],
      );
    });
  }

  async listAccessibleIds(
    orgId: string,
    userId: string,
    teamIds: string[],
  ): Promise<string[]> {
    const me = oidOf(userId);
    if (!me) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ id: string }>(
        `/* pipeshub:content.project_accessible_ids */ SELECT p.id FROM content.projects p WHERE p.org_id = $1 AND NOT p.is_deleted
           AND (p.user_id = $2::text OR p.visibility = 'org'
                OR EXISTS (SELECT 1 FROM content.project_members m WHERE m.org_id = $1 AND m.project_id = p.id
                             AND ((m.principal_type = 'user' AND m.principal_id = $2::text)
                               OR (m.principal_type = 'team' AND m.principal_id = ANY($3::text[])))))`,
        [k.orgId, me, teamIds],
      );
      return rows.map((r) => r.id);
    });
  }

  async setPinned(
    id: string,
    userId: string,
    pinned: boolean,
    clearLegacyPin: boolean,
  ): Promise<ProjectDto | null> {
    const oid = oidOf(id);
    const me = oidOf(userId);
    if (!oid || !me) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      if (!(await lockLive(k, oid))) return null;
      await k.query(
        `/* pipeshub:content.project_set_pinned */ UPDATE content.projects SET
            pinned_by = CASE WHEN $4::boolean THEN (CASE WHEN $3::text = ANY(pinned_by) THEN pinned_by ELSE array_append(pinned_by, $3::text::content.oid24) END)
                             ELSE array_remove(pinned_by, $3::text::content.oid24) END,
            is_pinned = CASE WHEN NOT $4::boolean AND $5::boolean THEN false ELSE is_pinned END,
            updated_at = $6
          WHERE org_id = $1 AND id = $2`,
        [k.orgId, oid, me, pinned, clearLegacyPin, touchedAt()],
      );
      return load(k, oid);
    });
  }

  async orgHasTeamMembers(orgId: string): Promise<boolean> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ found: boolean }>(
        `/* pipeshub:content.project_org_has_teams */ SELECT EXISTS (SELECT 1 FROM content.projects p WHERE p.org_id = $1 AND NOT p.is_deleted
           AND EXISTS (SELECT 1 FROM content.project_members m WHERE m.org_id = $1 AND m.project_id = p.id AND m.principal_type = 'team')) AS found`,
        [k.orgId],
      );
      return rows[0]?.found === true;
    });
  }

  async isLiveWithoutTeamMembers(id: string): Promise<boolean> {
    const oid = oidOf(id);
    if (!oid) return false;
    return inUnit(undefined, { write: false }, async (k) => {
      const { rows } = await k.query<{ found: boolean }>(
        `/* pipeshub:content.project_without_teams */ SELECT EXISTS (SELECT 1 FROM content.projects p WHERE p.org_id = $1 AND p.id = $2 AND NOT p.is_deleted
           AND NOT EXISTS (SELECT 1 FROM content.project_members m WHERE m.org_id = $1 AND m.project_id = p.id AND m.principal_type = 'team')) AS found`,
        [k.orgId, oid],
      );
      return rows[0]?.found === true;
    });
  }

  async touchActivity(id: string): Promise<void> {
    const oid = oidOf(id);
    if (!oid) return;
    await inUnit(undefined, { write: true }, async (k) => {
      const now = touchedAt();
      await k.query(
        '/* pipeshub:content.project_touch */ UPDATE content.projects SET last_activity_at = $3, updated_at = $3 WHERE org_id = $1 AND id = $2',
        [k.orgId, oid, now],
      );
    });
  }

  async claimLinkedKb(
    id: string,
    previousKbId: string | null,
    kbId: string,
  ): Promise<ProjectDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      const done = await k.query(
        `/* pipeshub:content.project_claim_kb */ UPDATE content.projects SET linked_knowledge_base_id = $4, updated_at = $5
          WHERE org_id = $1 AND id = $2 AND linked_knowledge_base_id IS NOT DISTINCT FROM $3::text`,
        [
          k.orgId,
          oid,
          previousKbId,
          stripNul(kbId, 'project', 'kb'),
          touchedAt(),
        ],
      );
      return done.rowCount === 1 ? load(k, oid) : null;
    });
  }
}
