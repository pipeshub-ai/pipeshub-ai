/**
 * PostgreSQL implementation of the saved-search repository (`DOC_STORE=postgres`). Same conventions as the conversation
 * repository: the org is `$1` in every statement and equals the unit's org; hits live in `conversation.citations`.
 */
import { BadRequestError } from '../../errors/http.errors';
import { insertCitation } from '../conversation/pg';
import { stripNul } from '../nul';
import { CitationSqlRow, citationDto } from '../conversation/pg-rows';
import {
  Params,
  assertOrg,
  fill,
  inUnit,
  newObjectId,
  oidOf,
  oidsOf,
} from '../pg-util';
import type { Tx as PgKernelTx } from '../../db';
import { replaceMembers } from '../members';
import type { Tx } from '../uow';
import type {
  CitationDto,
  NewSemanticSearch,
  SearchListQuery,
  SearchQuery,
  SearchSharing,
  SemanticSearchDto,
  SemanticSearchRepository,
} from './types';

interface SearchRow {
  id: string;
  query: string;
  result_limit: number;
  user_id: string;
  citation_ids: string[];
  records: Record<string, string>;
  is_shared: boolean;
  share_link: string | null;
  is_archived: boolean;
  archived_by: string | null;
  version: number;
  nulls: string[];
  created_at: Date;
  updated_at: Date;
  shared_with: Array<{
    userId: string;
    accessLevel: 'read' | 'write';
    _id: string;
  }>;
}

const COLS = `s.id, s.query, s.result_limit, s.user_id, s.citation_ids::text[] AS citation_ids, s.records, s.is_shared, s.share_link,
  s.is_archived, s.archived_by, s.version, s.nulls, s.created_at, s.updated_at,
  COALESCE((SELECT jsonb_agg(jsonb_build_object('userId', m.user_id, 'accessLevel', m.access_level, '_id', m.entry_id) ORDER BY m.pos)
    FROM conversation.semantic_search_members m WHERE m.org_id = $1 AND m.search_id = s.id), '[]'::jsonb) AS shared_with`;
const sql = (text: string, extra: Record<string, string> = {}): string =>
  fill(text, { ...extra, COLS });

function dto(
  r: SearchRow,
  orgId: string,
  hits?: Map<string, CitationDto>,
): SemanticSearchDto {
  const o: Record<string, unknown> = {
    _id: r.id,
    query: r.query,
    limit: r.result_limit,
    orgId,
    userId: r.user_id,
    citationIds: hits
      ? r.citation_ids.flatMap((id) => (hits.has(id) ? [hits.get(id)] : []))
      : r.citation_ids,
    records: r.records,
    isShared: r.is_shared,
    sharedWith: r.shared_with,
    isArchived: r.is_archived,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    __v: r.version,
  };
  if (r.share_link !== null) o.shareLink = r.share_link;
  if (r.archived_by !== null) o.archivedBy = r.archived_by;
  else if (r.nulls.includes('archived_by')) o.archivedBy = null;
  return o as unknown as SemanticSearchDto;
}

/** The access conditions of the old `buildFilter` for searches, with the org as `$1`. */
function visible(
  p: Params,
  orgId: string,
  userId: string,
  q: SearchQuery,
  opts: { id?: string; archived?: boolean } = {},
): string {
  const owned = q.owned ?? true;
  const shared = q.shared ?? true;
  if (!owned && !shared)
    throw new BadRequestError('Either owned or shared must be true');
  p.add(orgId);
  const user = p.add(userId);
  const access = [
    ...(owned ? [`s.user_id = ${user}::text`] : []),
    ...(shared
      ? [
          `(s.is_shared AND EXISTS (SELECT 1 FROM conversation.semantic_search_members w WHERE w.org_id = $1 AND w.search_id = s.id AND w.user_id = ${user}::text))`,
        ]
      : []),
  ];
  const where = [
    's.org_id = $1',
    `s.is_archived = ${p.add(opts.archived ?? false)}`,
    `(${access.join(' OR ')})`,
  ];
  if (opts.id) where.push(`s.id = ${p.add(opts.id)}::text`);
  if (q.startDate) where.push(`s.created_at >= ${p.add(q.startDate)}`);
  if (q.endDate) where.push(`s.created_at <= ${p.add(q.endDate)}`);
  if (q.isShared !== undefined)
    where.push(`s.is_shared = ${p.add(q.isShared)}`);
  return where.join(' AND ');
}

async function hitsOf(
  k: PgKernelTx,
  rows: SearchRow[],
): Promise<Map<string, CitationDto>> {
  const ids = oidsOf(rows.flatMap((r) => r.citation_ids));
  const out = new Map<string, CitationDto>();
  if (!ids.length) return out;
  const { rows: found } = await k.query<CitationSqlRow>(
    `/* pipeshub:search.populate_hits */ SELECT c.id, c.content, c.chunk_index, c.citation_type, c.metadata, c.version, c.created_at, c.updated_at
       FROM conversation.citations c WHERE c.org_id = $1 AND c.id = ANY($2::text[])`,
    [k.orgId, ids],
  );
  for (const c of found)
    out.set(c.id, citationDto(c, false) as unknown as CitationDto);
  return out;
}

export class PgSemanticSearchRepository implements SemanticSearchRepository {
  async create(input: NewSemanticSearch, tx?: Tx): Promise<SemanticSearchDto> {
    return inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, input.orgId);
      const hits: string[] = [];
      for (const h of input.hits ?? [])
        hits.push((await insertCitation(k, h))._id);
      const now = new Date();
      const { rows } = await k.query<SearchRow>(
        sql(`/* pipeshub:search.create */ INSERT INTO conversation.semantic_searches AS s
          (org_id, id, query, result_limit, user_id, citation_ids, records, created_at, updated_at)
          VALUES ($1, $2, $3, $4, $5, $6::text[], $7::jsonb, $8, $8) RETURNING @{COLS}`),
        [
          k.orgId,
          newObjectId(),
          stripNul(input.query, 'semantic-search', 'query'),
          input.limit,
          input.userId,
          hits,
          JSON.stringify(
            stripNul(
              Object.fromEntries(input.records),
              'semantic-search',
              'records',
            ),
          ),
          now,
        ],
      );
      return dto(rows[0] as SearchRow, k.orgId);
    });
  }

  async findVisible(
    orgId: string,
    viewerId: string,
    id: string,
    opts: {
      archived?: boolean;
      populateCitations?: boolean;
      query?: SearchQuery;
    } = {},
  ): Promise<SemanticSearchDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const where = visible(p, orgId, viewerId, opts.query ?? {}, {
        id: oid,
        archived: opts.archived,
      });
      const { rows } = await k.query<SearchRow>(
        sql(
          '/* pipeshub:search.find_visible */ SELECT @{COLS} FROM conversation.semantic_searches s WHERE @{WHERE} LIMIT 1',
          { WHERE: where },
        ),
        p.values,
      );
      if (!rows[0]) return null;
      return dto(
        rows[0],
        k.orgId,
        opts.populateCitations ? await hitsOf(k, rows) : undefined,
      );
    });
  }

  async listVisible(
    orgId: string,
    viewerId: string,
    q: SearchListQuery,
  ): Promise<{ rows: SemanticSearchDto[]; total: number }> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const where = visible(p, orgId, viewerId, q);
      const countValues = [...p.values];
      // the stored searches have no activity time or title: those sorts leave only the id order, as the document store did
      const by =
        q.sort?.field === 'createdAt'
          ? `s.created_at ${q.sort.direction === 1 ? 'ASC NULLS FIRST' : 'DESC NULLS LAST'}, `
          : '';
      const off = p.add(q.skip);
      const lim = p.add(q.limit);
      const [rows, total] = await Promise.all([
        k.query<SearchRow>(
          sql(
            '/* pipeshub:search.list_visible */ SELECT @{COLS} FROM conversation.semantic_searches s WHERE @{WHERE} ORDER BY @{BY}s.id @{DIR} OFFSET @{OFF} LIMIT @{LIM}',
            {
              WHERE: where,
              BY: by,
              DIR: q.sort?.direction === 1 ? 'ASC' : 'DESC',
              OFF: off,
              LIM: lim,
            },
          ),
          p.values,
        ),
        k.query<{ n: number }>(
          sql(
            '/* pipeshub:search.count_visible */ SELECT count(*)::int AS n FROM conversation.semantic_searches s WHERE @{WHERE}',
            { WHERE: where },
          ),
          countValues,
        ),
      ]);
      return {
        rows: rows.rows.map((r) => dto(r, k.orgId)),
        total: total.rows[0]?.n ?? 0,
      };
    });
  }

  /** Returns the row as it was before the change, as the callers' responses expect. */
  private async before(
    k: PgKernelTx,
    where: string,
    values: unknown[],
  ): Promise<SemanticSearchDto | null> {
    const { rows } = await k.query<SearchRow>(
      sql(
        '/* pipeshub:search.lock_one */ SELECT @{COLS} FROM conversation.semantic_searches s WHERE @{WHERE} LIMIT 1',
        { WHERE: where },
      ),
      values,
    );
    return rows[0] ? dto(rows[0], k.orgId) : null;
  }

  async setSharing(
    id: string,
    sharing: SearchSharing,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(tx, { write: true }, async (k) => {
      const prev = await this.before(k, 's.org_id = $1 AND s.id = $2::text', [
        k.orgId,
        oid,
      ]);
      if (!prev) return null;
      await k.query(
        `/* pipeshub:search.set_sharing */ UPDATE conversation.semantic_searches SET
            is_shared = CASE WHEN $3::boolean THEN $4::boolean ELSE is_shared END,
            share_link = CASE WHEN $5::boolean THEN $6 ELSE share_link END, updated_at = $7
          WHERE org_id = $1 AND id = $2::text`,
        [
          k.orgId,
          oid,
          sharing.isShared !== undefined,
          sharing.isShared ?? false,
          sharing.shareLink !== undefined,
          sharing.shareLink ?? null,
          new Date(),
        ],
      );
      await replaceMembers(k, 'search', oid, sharing.sharedWith);
      return prev;
    });
  }

  async unshare(
    id: string,
    remaining: SearchSharing['sharedWith'],
    tx?: Tx,
  ): Promise<SemanticSearchDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(tx, { write: true }, async (k) => {
      const where = 's.org_id = $1 AND s.id = $2::text';
      if (!(await this.before(k, where, [k.orgId, oid]))) return null;
      // With nobody left the search stops being shared and loses its link.
      await k.query(
        `/* pipeshub:search.unshare */ UPDATE conversation.semantic_searches SET
            is_shared = CASE WHEN $3::boolean THEN false ELSE is_shared END,
            share_link = CASE WHEN $3::boolean THEN NULL ELSE share_link END, updated_at = $4
          WHERE org_id = $1 AND id = $2::text`,
        [k.orgId, oid, remaining.length === 0, new Date()],
      );
      await replaceMembers(k, 'search', oid, remaining);
      return this.before(k, where, [k.orgId, oid]);
    });
  }

  async archive(
    id: string,
    byUserId: string,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(tx, { write: true }, async (k) => {
      const prev = await this.before(k, 's.org_id = $1 AND s.id = $2::text', [
        k.orgId,
        oid,
      ]);
      if (!prev) return null;
      await k.query(
        `/* pipeshub:search.archive */ UPDATE conversation.semantic_searches SET is_archived = true, archived_by = $3,
            nulls = array_remove(nulls, 'archived_by'), updated_at = $4 WHERE org_id = $1 AND id = $2::text`,
        [k.orgId, oid, byUserId, new Date()],
      );
      return prev;
    });
  }

  async unarchive(
    orgId: string,
    viewerId: string,
    id: string,
    query?: SearchQuery,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const where = visible(p, orgId, viewerId, query ?? {}, {
        id: oid,
        archived: true,
      });
      const prev = await this.before(k, where, p.values);
      if (!prev) return null;
      await k.query(
        `/* pipeshub:search.unarchive */ UPDATE conversation.semantic_searches SET is_archived = false, archived_by = NULL,
            nulls = array_append(array_remove(nulls, 'archived_by'), 'archived_by'), updated_at = $3 WHERE org_id = $1 AND id = $2::text`,
        [k.orgId, oid, new Date()],
      );
      return prev;
    });
  }

  private async remove(k: PgKernelTx, ids: string[]): Promise<void> {
    if (!ids.length) return;
    const gone = await k.query<{ citation_ids: string[] }>(
      `/* pipeshub:search.delete */ DELETE FROM conversation.semantic_searches WHERE org_id = $1 AND id = ANY($2::text[])
        RETURNING citation_ids::text[] AS citation_ids`,
      [k.orgId, ids],
    );
    const hits = oidsOf(gone.rows.flatMap((r) => r.citation_ids));
    if (hits.length)
      await k.query(
        '/* pipeshub:search.delete_hits */ DELETE FROM conversation.citations WHERE org_id = $1 AND id = ANY($2::text[])',
        [k.orgId, hits],
      );
  }

  async delete(
    orgId: string,
    viewerId: string,
    id: string,
    query?: SearchQuery,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const where = visible(p, orgId, viewerId, query ?? {}, { id: oid });
      const prev = await this.before(k, where, p.values);
      if (!prev) return null;
      await this.remove(k, [oid]);
      return prev;
    });
  }

  async deleteVisible(
    orgId: string,
    viewerId: string,
    q: SearchQuery,
    tx?: Tx,
  ): Promise<number> {
    return inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const where = visible(p, orgId, viewerId, q);
      const { rows } = await k.query<{ id: string }>(
        sql(
          '/* pipeshub:search.ids_visible */ SELECT s.id FROM conversation.semantic_searches s WHERE @{WHERE} FOR UPDATE OF s',
          { WHERE: where },
        ),
        p.values,
      );
      await this.remove(
        k,
        rows.map((r) => r.id),
      );
      return rows.length;
    });
  }

  async findCitations(orgId: string, ids: string[]): Promise<CitationDto[]> {
    const want = oidsOf(ids);
    if (!want.length) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<CitationSqlRow>(
        `/* pipeshub:search.find_citations */ SELECT c.id, c.content, c.chunk_index, c.citation_type, c.metadata, c.version, c.created_at, c.updated_at
           FROM conversation.citations c WHERE c.org_id = $1 AND c.id = ANY($2::text[]) ORDER BY c.id`,
        [k.orgId, want],
      );
      return rows.map((r) => citationDto(r, false) as unknown as CitationDto);
    });
  }
}
