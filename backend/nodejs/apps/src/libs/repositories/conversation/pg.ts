/**
 * PostgreSQL implementation of the conversation repository (`DOC_STORE=postgres`).
 *
 * Every statement names the org as `$1` in every reference to a table (the row policy is the second line of defence,
 * not the first; an extension that partitions by org prunes on it). The org comes from the unit of work (the ambient org context); an
 * `orgId` argument must equal it. Methods given a `tx` use it; methods that are not, join the unit already open in
 * the async context or open their own. The run lease always opens its own: it must never share a transaction with
 * a turn.
 *
 * Statements are static text with `@{NAME}` markers for shared column lists and predicates; values are always
 * parameters. Author-only message columns (tools, reasoning, parts, reference data) are projected per row inside
 * SQL, so another viewer's copy is never read from the table.
 */
import { randomUUID } from 'crypto';
import { pgRuntime } from '../../db';
import type { Tx as PgKernelTx } from '../../db';
import { CONVERSATION_STATUS } from '../../../modules/enterprise_search/constants/constants';
import { BadRequestError, InternalServerError } from '../../errors/http.errors';
import { ConflictError, NotFoundError } from '../errors';
import { stripNul } from '../nul';
import {
  Params,
  assertOrg,
  fill,
  inUnit,
  likeEscape,
  newObjectId,
  oidOf,
  oidsOf,
  unescapeRegex,
} from '../pg-util';
import { replaceMembers } from '../members';
import type { IChatAttachmentRef } from '../../../modules/enterprise_search/types/conversation.interfaces';
import type { Tx } from '../uow';
import {
  CitationSqlRow,
  MODEL_FIELDS,
  MessageSqlRow,
  SessionRow,
  citationDto,
  messageDto,
  sessionDto,
  storedCitationMetadata,
  storedAttachments,
  storedFeedback,
  storedMessage,
  storedModelInfo,
} from './pg-rows';
import type {
  AgentArchiveGroup,
  ArchivedSearchResult,
  AttachmentsAndCitedRecords,
  ChatSessionDto,
  CitationDto,
  ConversationRepository,
  MessageDto,
  MessageRow,
  NewCitation,
  NewMessage,
  NewSession,
  ContentMatches,
  VisibleSummaryRow,
  RunReservation,
  RunStateChange,
  SessionActor,
  SessionListQuery,
  SessionPage,
  SessionScope,
  SessionShareDto,
  SessionSummary,
} from './types';

const ACTIVE_RUN_WINDOW_MS = 10 * 60 * 1000;
const ACTIVE_RUN_SETTLE_MS = 15_000;
const ACTIVE_RUN_POLL_MS = 250;
const LIVE = CONVERSATION_STATUS.INPROGRESS;

const SESSION_COLS = `s.id, s.session_type, s.user_id, s.initiator, s.title, s.is_shared, s.share_link, s.is_deleted, s.deleted_by,
  s.is_archived, s.archived_by, s.last_activity_at, s.status, s.fail_reason, s.next_seq, s.model_info, s.conversation_errors, s.metadata,
  s.agent_key, s.conversation_source, s.compacted_summary, s.compacted_at_turn_index, s.compacted_at_timestamp, s.project_id,
  s.project_visibility, s.version, s.nulls, s.is_pinned, s.created_at, s.updated_at`;
const SHARED_WITH = `COALESCE((SELECT jsonb_agg(jsonb_build_object('userId', m.user_id, 'accessLevel', m.access_level, '_id', m.entry_id) ORDER BY m.pos)
  FROM conversation.chat_session_members m WHERE m.org_id = $1 AND m.session_id = s.id), '[]'::jsonb) AS shared_with`;

const MESSAGE_COLS = `m.id, m.session_id, m.seq, m.message_type, m.content, m.content_format, m.status, m.confidence,
  m.citations, m.follow_up_questions, m.feedback, m.metadata, m.model_info, m.applied_filters, m.attachments, m.reference_data,
  m.tools, m.reasoning, m.parts, m.created_at, m.updated_at`;

const CITATION_COLS = `c.id, c.content, c.chunk_index, c.citation_type, c.metadata, c.version, c.created_at, c.updated_at`;

/** The user is `$5` wherever these are used. */
const IS_WRITER = `EXISTS (SELECT 1 FROM conversation.chat_session_members w
  WHERE w.org_id = $1 AND w.session_id = s.id AND w.user_id = $5::text AND w.access_level = 'write')`;
const IS_MEMBER = `EXISTS (SELECT 1 FROM conversation.chat_session_members w
  WHERE w.org_id = $1 AND w.session_id = s.id AND w.user_id = $5::text)`;
/** The owner. A member a chat is shared with can read it but not continue, cancel or regenerate it. */
const OWNER = `s.user_id = $5::text`;
/** Who may delete or archive a chat: the person who started it, or a member it was shared with for writing. */
const INITIATOR_OR_WRITER = `(s.user_id = $5::text AND (s.initiator = $5::text OR ${IS_WRITER}))`;
/** An agent chat for the owner, or for a member it is shared with (at write level when asked); an archived chat is not shared on. */
const AGENT_READ = `(s.user_id = $5::text OR (s.is_shared AND NOT s.is_archived AND ${IS_MEMBER}))`;
const AGENT_WRITE = `(s.user_id = $5::text OR (s.is_shared AND NOT s.is_archived AND ${IS_WRITER}))`;

const MACROS = {
  S: `${SESSION_COLS}, ${SHARED_WITH}`,
  SC: SESSION_COLS,
  M: MESSAGE_COLS,
  C: CITATION_COLS,
  OW: OWNER,
  W: IS_WRITER,
  MEM: IS_MEMBER,
};
const SQL = (text: string, extra: Record<string, string> = {}): string =>
  fill(text, { ...extra, ...MACROS });

/** Parameters, in this order, of every `FIND`: org, id, kind or null, agent key or null, user. */
const FIND = `/* pipeshub:conversation.find_session */ SELECT @{S} FROM conversation.chat_sessions s
  WHERE s.org_id = $1 AND s.id = $2::text AND ($3::text IS NULL OR s.session_type = $3) AND ($4::text IS NULL OR s.agent_key = $4)
    AND NOT s.is_deleted AND @{PRED}`;
const BY_ID = SQL(
  `/* pipeshub:conversation.session_by_id */ SELECT @{S} FROM conversation.chat_sessions s WHERE s.org_id = $1 AND s.id = $2::text`,
);
const find = (predicate: string): string => SQL(FIND, { PRED: predicate });

const FIND_FOR_TURN = find('@{OW}');
const FIND_AGENT_WRITE = find(AGENT_WRITE);
const FIND_OWNED_BY_INITIATOR = find(
  's.user_id = $5::text AND s.initiator = $5::text',
);
const FIND_OWNED = find('s.user_id = $5::text');
const FIND_FOR_SHARE = find('s.user_id = $5::text AND s.initiator = $5::text');
const FIND_FOR_UNSHARE = find('s.initiator = $5::text');
const FIND_DELETABLE = find(INITIATOR_OR_WRITER);
const FIND_FOR_FEEDBACK_CHAT = find(
  '(s.initiator = $5::text OR (s.is_shared AND @{MEM}))',
);
const FIND_AGENT_READ = find(AGENT_READ);

const LEAN_SHAPE = { doc: false, version: false } as const;
const DOC_SHAPE = { doc: true, version: true } as const;

const scopeArgs = (scope: SessionScope): [string, string | null] => [
  scope.kind,
  scope.kind === 'agent' ? (scope.agentKey ?? null) : null,
];

const SORT_COLUMNS: Record<string, string> = {
  isPinned: 'is_pinned',
  lastActivityAt: 'last_activity_at',
  createdAt: 'created_at',
  updatedAt: 'updated_at',
  title: 'title',
  _id: 'id',
};

/** Ties follow the first sort key's direction (descending when there is none): equal timestamps keep creation order. */
const tieDirection = (sort: Record<string, 1 | -1>): 'ASC' | 'DESC' =>
  Object.values(sort)[0] === 1 ? 'ASC' : 'DESC';

/** The document store's order: missing values first when ascending, last when descending; the id breaks ties. */
function orderBy(sort: Record<string, 1 | -1>, alias: string): string {
  const parts = Object.entries(sort)
    .filter(([key]) => SORT_COLUMNS[key])
    .map(
      ([key, dir]) =>
        `${alias}${SORT_COLUMNS[key]} ${dir === 1 ? 'ASC NULLS FIRST' : 'DESC NULLS LAST'}`,
    );
  if (!('_id' in sort)) parts.push(`${alias}id ${tieDirection(sort)}`);
  return parts.join(', ');
}

const likeOf = (escapedRegex: string): string =>
  `%${likeEscape(unescapeRegex(escapedRegex))}%`;

/** The ways a viewer can see sessions in a typed query, and the conditions every row meets (`s.org_id = $1` first). */
interface Plan {
  common: string;
  user: string;
  owned: boolean;
  member?: { writeOnly: boolean };
  /** the placeholder holding the accessible project ids */
  project?: string;
  /** the same three ways as one condition, for a lookup by id */
  access: string;
}

export function planOf(q: SessionListQuery, p: Params): Plan {
  p.add(q.orgId); // $1
  const user = p.add(q.userId);
  const common = [
    's.org_id = $1',
    'NOT s.is_deleted',
    q.scope.kind === 'chat' &&
    q.shared &&
    !q.owned &&
    q.sharedIncludesAgentChats
      ? "s.session_type IN ('chat', 'agent')"
      : `s.session_type = ${p.add(q.scope.kind)}`,
  ];
  if (q.scope.kind === 'agent' && q.scope.agentKey)
    common.push(`s.agent_key = ${p.add(q.scope.agentKey)}`);
  const chat = q.scope.kind === 'chat';
  // a chat list defaults to unarchived; 'only' replaces that, an agent list has no default
  if (chat && q.archived !== 'only') common.push('NOT s.is_archived');
  if (q.id) common.push(`s.id = ${p.add(oidOf(q.id) ?? '')}::text`);
  if (q.projectFilter?.kind === 'unassigned')
    common.push('s.project_id IS NULL');
  else if (q.projectFilter)
    common.push(
      `s.project_id = ${p.add(oidOf(q.projectFilter.id) ?? '')}::text`,
    );
  if (q.search !== undefined) {
    const title = `s.title ILIKE ${p.add(likeOf(q.search))}`;
    const ids = oidsOf(q.contentMatchIds ?? []);
    common.push(
      ids.length ? `(${title} OR s.id = ANY(${p.add(ids)}::text[]))` : title,
    );
  }
  if (q.startDate) common.push(`s.created_at >= ${p.add(q.startDate)}`);
  if (q.endDate) common.push(`s.created_at <= ${p.add(q.endDate)}`);
  if (q.isSharedFilter !== undefined)
    common.push(`s.is_shared = ${p.add(q.isSharedFilter)}`);
  if (q.archived === 'notTrue') common.push('NOT s.is_archived');
  else if (q.archived === 'only')
    common.push(
      `s.is_archived AND (s.archived_by IS NOT NULL OR 'archived_by' = ANY(s.nulls))`,
    );
  const projects = oidsOf(q.accessibleProjectIds ?? []);
  const plan: Plan = {
    common: common.join(' AND '),
    user,
    owned: false,
    access: '',
  };
  const access: string[] = [];
  const memberSql = (writeOnly: boolean): string =>
    `(s.is_shared${chat ? '' : ' AND NOT s.is_archived'} AND EXISTS (SELECT 1 FROM conversation.chat_session_members w WHERE w.org_id = $1 AND w.session_id = s.id AND w.user_id = ${user}::text${writeOnly ? " AND w.access_level = 'write'" : ''}))`;
  const writeOnly = !chat && q.sharedAccess === 'write';
  if (!chat || q.owned) {
    plan.owned = true;
    access.push(`s.user_id = ${user}::text`);
  }
  if (chat ? q.shared : q.sharedAccess) {
    plan.member = { writeOnly };
    access.push(memberSql(writeOnly));
  }
  if (projects.length && (chat ? q.shared : true)) {
    plan.project = p.add(projects);
    access.push(
      `(s.project_id = ANY(${plan.project}::text[]) AND s.project_visibility = 'project'${chat ? '' : ' AND NOT s.is_archived'})`,
    );
  }
  plan.access = access.length ? `(${access.join(' OR ')})` : 'FALSE';
  return plan;
}

/** The ways to a session, each a static statement fragment (the org is `$1`); @{LEVEL} narrows a membership to write access. */
const WAY = {
  owned: {
    page: '(SELECT @{COLS} FROM conversation.chat_sessions s WHERE @{COMMON} AND s.user_id = @{USER}::text ORDER BY @{ORDER_S} LIMIT @{TAKE})',
    count:
      '/* pipeshub:conversation.count_sessions */ SELECT s.id FROM conversation.chat_sessions s WHERE @{COMMON} AND s.user_id = @{USER}::text',
  },
  member: {
    page: `(SELECT @{COLS} FROM conversation.chat_session_members w JOIN conversation.chat_sessions s ON s.org_id = $1 AND s.id = w.session_id
       WHERE w.org_id = $1 AND w.user_id = @{USER}::text @{LEVEL} AND s.is_shared @{ARCH} AND @{COMMON} ORDER BY @{ORDER_S} LIMIT @{TAKE})`,
    count: `/* pipeshub:conversation.count_sessions */ SELECT s.id FROM conversation.chat_session_members w JOIN conversation.chat_sessions s ON s.org_id = $1 AND s.id = w.session_id
       WHERE w.org_id = $1 AND w.user_id = @{USER}::text @{LEVEL} AND s.is_shared @{ARCH} AND @{COMMON}`,
  },
  project: {
    page: `(SELECT x.* FROM unnest(@{PROJECTS}::text[]) AS pr(pid), LATERAL (SELECT @{COLS} FROM conversation.chat_sessions s
       WHERE @{COMMON} AND s.project_id = pr.pid AND s.project_visibility = 'project' @{ARCH} ORDER BY @{ORDER_S} LIMIT @{TAKE}) x)`,
    count: `/* pipeshub:conversation.count_sessions */ SELECT s.id FROM unnest(@{PROJECTS}::text[]) AS pr(pid) JOIN conversation.chat_sessions s ON s.org_id = $1
       AND s.project_id = pr.pid AND s.project_visibility = 'project' @{ARCH} WHERE @{COMMON}`,
  },
};
const LIST = `/* pipeshub:conversation.list_sessions */ SELECT * FROM (SELECT DISTINCT ON (c.id) c.* FROM (@{BRANCHES}) c ORDER BY c.id) d
  ORDER BY @{ORDER} OFFSET @{OFF} LIMIT @{LIM}`;
const COUNT =
  '/* pipeshub:conversation.count_sessions */ SELECT count(*)::int AS n FROM (@{WAYS}) u';

/**
 * One index-ordered branch per way of seeing a session (own, member of, in a shared project), each stopping at
 * `skip + limit` rows, then one merge. Not one `OR` across the ways, which would scan the whole org.
 */
export function pageStatement(
  q: SessionListQuery,
  withMembers: boolean,
): { text: string; values: unknown[] } | undefined {
  const p = new Params();
  const plan = planOf(q, p);
  if (!plan.owned && !plan.member && !plan.project) return undefined;
  const m = {
    COLS: withMembers ? MACROS.S : SESSION_COLS,
    COMMON: plan.common,
    USER: plan.user,
    LEVEL: plan.member?.writeOnly ? "AND w.access_level = 'write'" : '',
    // the owner archiving an agent chat takes it away from the people it was shared with
    ARCH: q.scope.kind === 'agent' ? 'AND NOT s.is_archived' : '',
    TAKE: p.add(q.skip + q.limit),
    ORDER_S: orderBy(q.sort, 's.'),
    PROJECTS: plan.project ?? '',
  };
  const branches = [
    ...(plan.owned ? [fill(WAY.owned.page, m)] : []),
    ...(plan.member ? [fill(WAY.member.page, m)] : []),
    ...(plan.project ? [fill(WAY.project.page, m)] : []),
  ];
  const text = fill(LIST, {
    BRANCHES: branches.join(' UNION ALL '),
    ORDER: orderBy(q.sort, 'd.'),
    OFF: p.add(q.skip),
    LIM: p.add(q.limit),
  });
  return { text, values: p.values };
}

/** The exact number of sessions the query reaches, without the page: the same ways, deduplicated by `UNION`. */
export function countStatement(q: SessionListQuery): {
  text: string;
  values: unknown[];
} {
  const p = new Params();
  const plan = planOf(q, p);
  const m = {
    COMMON: plan.common,
    USER: plan.user,
    LEVEL: plan.member?.writeOnly ? "AND w.access_level = 'write'" : '',
    ARCH: q.scope.kind === 'agent' ? 'AND NOT s.is_archived' : '',
    PROJECTS: plan.project ?? '',
  };
  const ways = [
    ...(plan.owned ? [fill(WAY.owned.count, m)] : []),
    ...(plan.member ? [fill(WAY.member.count, m)] : []),
    ...(plan.project ? [fill(WAY.project.count, m)] : []),
  ];
  return {
    text: fill(COUNT, { WAYS: ways.join(' UNION ') }),
    values: p.values,
  };
}

async function citationDocs(
  k: PgKernelTx,
  ids: string[],
): Promise<Map<string, CitationDto | undefined>> {
  const out = new Map<string, CitationDto | undefined>();
  const want = oidsOf(ids);
  if (!want.length) return out;
  const { rows } = await k.query<CitationSqlRow>(
    SQL(`/* pipeshub:conversation.citations_for_messages */ SELECT @{C} FROM conversation.citations c
      WHERE c.org_id = $1 AND c.id = ANY($2::text[])`),
    [k.orgId, want],
  );
  for (const r of rows) out.set(r.id, citationDto(r, false));
  return out;
}

/** One citation row, in the org the unit runs as (the `orgId` inside its metadata is data from the answering service and is kept as given). */
export async function insertCitation(
  k: PgKernelTx,
  raw: NewCitation,
): Promise<CitationDto> {
  const c = stripNul(raw, 'conversation', 'citation');
  const metadata = storedCitationMetadata(c);
  const { rows } = await k.query<CitationSqlRow>(
    SQL(`/* pipeshub:conversation.create_citation */ INSERT INTO conversation.citations AS c
      (org_id, id, content, chunk_index, citation_type, record_id, record_name, metadata, created_at, updated_at)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $9) RETURNING @{C}`),
    [
      k.orgId,
      newObjectId(),
      c.content,
      c.chunkIndex,
      c.citationType,
      String(metadata.recordId),
      typeof metadata.recordName === 'string' ? metadata.recordName : null,
      JSON.stringify(metadata),
      new Date(),
    ],
  );
  return citationDto(rows[0] as CitationSqlRow, true);
}

const citationIdsOf = (rows: MessageSqlRow[]): string[] =>
  rows.flatMap((r) =>
    r.citations.map((c) =>
      typeof c.citationId === 'string' ? c.citationId : '',
    ),
  );

export class PgConversationRepository implements ConversationRepository {
  isConnected(): boolean {
    try {
      pgRuntime();
      return true;
    } catch {
      return false;
    }
  }

  private async one(
    k: PgKernelTx,
    text: string,
    values: unknown[],
    shape: Parameters<typeof sessionDto>[2],
  ): Promise<ChatSessionDto | null> {
    const { rows } = await k.query<SessionRow>(text, values);
    return rows[0] ? sessionDto(rows[0], k.orgId, shape) : null;
  }

  async findById(id: string): Promise<ChatSessionDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: false }, (k) =>
      this.one(k, BY_ID, [k.orgId, oid], DOC_SHAPE),
    );
  }

  // ---- sessions: create and read
  async openSession(
    fields: NewSession,
    firstMessage: NewMessage,
    tx?: Tx,
  ): Promise<ChatSessionDto> {
    return inUnit(tx, { write: true }, async (k) => {
      const saved = await this.insertSession(k, fields);
      await this.insertMessages(k, saved._id, [firstMessage]);
      return saved;
    });
  }

  createSession(fields: NewSession, tx?: Tx): Promise<ChatSessionDto> {
    return inUnit(tx, { write: true }, (k) => this.insertSession(k, fields));
  }

  private async insertSession(
    k: PgKernelTx,
    fields: NewSession,
  ): Promise<ChatSessionDto> {
    assertOrg(k, fields.orgId);
    const id = fields.id ? oidOf(fields.id) : newObjectId();
    if (!id) throw new BadRequestError('Invalid conversation id');
    const now = new Date();
    const { rows } = await k.query<SessionRow>(
      SQL(`/* pipeshub:conversation.open_session */ INSERT INTO conversation.chat_sessions AS s
        (org_id, id, session_type, user_id, initiator, title, last_activity_at, status, model_info, agent_key, conversation_source,
         project_id, project_visibility, metadata, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12, $13, $14::jsonb, $15, $15)
        RETURNING @{S}`),
      [
        k.orgId,
        id,
        fields.sessionType,
        fields.userId,
        fields.initiator,
        stripNul(fields.title ?? null, 'conversation', 'title'),
        new Date(fields.lastActivityAt),
        fields.status,
        JSON.stringify(storedModelInfo(fields.modelInfo)),
        fields.agentKey ?? null,
        fields.conversationSource ?? null,
        fields.projectId ?? null,
        fields.projectVisibility ?? null,
        fields.metadata ? JSON.stringify(fields.metadata) : null,
        now,
      ],
    );
    return sessionDto(rows[0] as SessionRow, k.orgId, {
      ...DOC_SHAPE,
      withInternals: true,
    });
  }

  private lookup(
    text: string,
    scope: SessionScope | undefined,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const oid = oidOf(id);
    if (!oid) return Promise.resolve(null);
    const [kind, key] = scope ? scopeArgs(scope) : [null, null];
    return inUnit(tx, { write: false }, (k) => {
      assertOrg(k, actor.orgId);
      return this.one(
        k,
        text,
        [k.orgId, oid, kind, key, actor.userId],
        DOC_SHAPE,
      );
    });
  }

  findForTurn(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    return this.lookup(FIND_FOR_TURN, scope, id, actor, tx);
  }

  findAgentWithAccess(
    id: string,
    agentKey: string,
    actor: SessionActor,
    level: 'read' | 'write',
  ): Promise<ChatSessionDto | null> {
    return this.lookup(
      level === 'write' ? FIND_AGENT_WRITE : FIND_AGENT_READ,
      { kind: 'agent', agentKey },
      id,
      actor,
    );
  }

  findOwnedByInitiator(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
  ): Promise<ChatSessionDto | null> {
    return this.lookup(FIND_OWNED_BY_INITIATOR, scope, id, actor);
  }

  findOwned(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    return this.lookup(FIND_OWNED, scope, id, actor, tx);
  }

  findForShare(
    id: string,
    actor: SessionActor,
    scope: SessionScope = { kind: 'chat' },
  ): Promise<ChatSessionDto | null> {
    return this.lookup(FIND_FOR_SHARE, scope, id, actor);
  }

  findForUnshare(
    id: string,
    actor: SessionActor,
    scope: SessionScope = { kind: 'chat' },
  ): Promise<ChatSessionDto | null> {
    return this.lookup(FIND_FOR_UNSHARE, scope, id, actor);
  }

  findDeletable(
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    return this.lookup(FIND_DELETABLE, { kind: 'chat' }, id, actor, tx);
  }

  findArchivable(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    return this.lookup(
      scope.kind === 'chat' ? FIND_DELETABLE : FIND_OWNED,
      scope,
      id,
      actor,
      tx,
    );
  }

  findForFeedback(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    return this.lookup(
      scope.kind === 'chat' ? FIND_FOR_FEEDBACK_CHAT : FIND_OWNED,
      scope,
      id,
      actor,
      tx,
    );
  }

  async findVisibleSummary(
    q: SessionListQuery,
  ): Promise<SessionSummary | null> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, q.orgId);
      const p = new Params();
      const plan = planOf(q, p);
      if (plan.access === 'FALSE') return null;
      const { rows } = await k.query<SessionRow>(
        SQL(
          '/* pipeshub:conversation.visible_summary */ SELECT @{S} FROM conversation.chat_sessions s WHERE @{COMMON} AND @{ACCESS} LIMIT 1',
          { COMMON: plan.common, ACCESS: plan.access },
        ),
        p.values,
      );
      if (!rows[0]) return null;
      const d = sessionDto(rows[0], k.orgId, LEAN_SHAPE) as unknown as Record<
        string,
        unknown
      >;
      const out: Record<string, unknown> = {};
      for (const f of [
        '_id',
        'orgId',
        'title',
        'initiator',
        'createdAt',
        'isShared',
        'sharedWith',
        'status',
        'failReason',
        'modelInfo',
        'projectId',
        'projectVisibility',
      ])
        if (d[f] !== undefined) out[f] = d[f];
      return out as unknown as SessionSummary;
    });
  }

  async listSessions(q: SessionListQuery): Promise<SessionPage> {
    const page = pageStatement(q, !q.omitSharedWith);
    if (!page) return { rows: [], total: 0 };
    const count = countStatement(q);
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, q.orgId);
      const [rows, total] = await Promise.all([
        k.query<SessionRow>(page.text, page.values),
        k.query<{ n: number }>(count.text, count.values),
      ]);
      return {
        rows: rows.rows.map((r) =>
          sessionDto(r, k.orgId, {
            ...LEAN_SHAPE,
            omitSharedWith: q.omitSharedWith,
          }),
        ),
        total: total.rows[0]?.n ?? 0,
      };
    });
  }

  async listAgentSharedWithMe(q: {
    orgId: string;
    userId: string;
    agentKey: string;
    status?: string;
    sort: Record<string, 1 | -1>;
    skip: number;
    limit: number;
  }): Promise<ChatSessionDto[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, q.orgId);
      const { rows } = await k.query<SessionRow>(
        SQL(
          `/* pipeshub:conversation.agent_shared_with_me */ SELECT @{SC} FROM conversation.chat_sessions s
            WHERE s.org_id = $1 AND s.session_type = 'agent' AND s.agent_key = $3 AND NOT s.is_deleted AND s.is_shared AND NOT s.is_archived
              AND ($4::text IS NULL OR s.status = $4)
              AND EXISTS (SELECT 1 FROM conversation.chat_session_members w WHERE w.org_id = $1 AND w.session_id = s.id AND w.user_id = $2::text)
            ORDER BY @{ORDER} OFFSET $5 LIMIT $6`,
          { ORDER: orderBy(q.sort, 's.') },
        ),
        [k.orgId, q.userId, q.agentKey, q.status ?? null, q.skip, q.limit],
      );
      return rows.map((r) =>
        sessionDto(r, k.orgId, { ...LEAN_SHAPE, omitSharedWith: true }),
      );
    });
  }

  async searchArchived(q: {
    orgId: string;
    userId: string;
    escapedSearch: string;
    contentMatchIds: string[];
    deletedAgentKeys: string[] | null;
    skip: number;
    limit: number;
  }): Promise<ArchivedSearchResult> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, q.orgId);
      const m = {
        BASE: `s.org_id = $1 AND NOT s.is_deleted AND s.is_archived AND (s.archived_by IS NOT NULL OR 'archived_by' = ANY(s.nulls))
          AND (s.title ILIKE $3 OR s.id = ANY($4::text[]))`,
        CHATS: `(s.session_type = 'chat' AND (s.user_id = $2::text OR (s.is_shared AND EXISTS (SELECT 1 FROM conversation.chat_session_members w
          WHERE w.org_id = $1 AND w.session_id = s.id AND w.user_id = $2::text))))`,
        AGENTS: `(s.session_type = 'agent' AND s.user_id = $2::text AND ($5::text[] IS NULL OR NOT COALESCE(s.agent_key = ANY($5::text[]), false)))`,
      };
      const args = [
        k.orgId,
        q.userId,
        likeOf(q.escapedSearch),
        oidsOf(q.contentMatchIds),
        q.deletedAgentKeys,
      ];
      const [rows, chats, agents] = await Promise.all([
        k.query<SessionRow>(
          SQL(
            `/* pipeshub:conversation.search_archived */ SELECT @{S} FROM conversation.chat_sessions s WHERE @{BASE} AND (@{CHATS} OR @{AGENTS})
              ORDER BY s.last_activity_at DESC NULLS LAST, s.id DESC OFFSET $6 LIMIT $7`,
            m,
          ),
          [...args, q.skip, q.limit],
        ),
        k.query<{ n: number }>(
          SQL(
            '/* pipeshub:conversation.search_archived_chats */ SELECT count(*)::int AS n FROM conversation.chat_sessions s WHERE @{BASE} AND @{CHATS}',
            m,
          ),
          args.slice(0, 4),
        ),
        k.query<{ n: number }>(
          SQL(
            '/* pipeshub:conversation.search_archived_agents */ SELECT count(*)::int AS n FROM conversation.chat_sessions s WHERE @{BASE} AND @{AGENTS}',
            m,
          ),
          args,
        ),
      ]);
      return {
        rows: rows.rows.map((r) => ({
          ...sessionDto(r, k.orgId, LEAN_SHAPE),
          source:
            r.session_type === 'agent'
              ? ('agent' as const)
              : ('assistant' as const),
        })),
        assistantCount: chats.rows[0]?.n ?? 0,
        agentCount: agents.rows[0]?.n ?? 0,
      };
    });
  }

  async listArchivedAgentGroups(q: {
    orgId: string;
    userId: string;
    deletedAgentKeys: string[] | null;
    agentSkip: number;
    agentLimit: number;
    chatsPerAgent: number;
  }): Promise<{ groups: AgentArchiveGroup[]; totalAgents: number }> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, q.orgId);
      const m = {
        WHERE: `s.org_id = $1 AND s.user_id = $2::text AND s.is_archived AND (s.archived_by IS NOT NULL OR 'archived_by' = ANY(s.nulls))
          AND NOT s.is_deleted AND s.session_type = 'agent' AND ($3::text[] IS NULL OR NOT COALESCE(s.agent_key = ANY($3::text[]), false))`,
      };
      const [total, groups] = await Promise.all([
        k.query<{ n: number }>(
          SQL(
            '/* pipeshub:conversation.archived_agent_count */ SELECT count(*)::int AS n FROM (SELECT 1 FROM conversation.chat_sessions s WHERE @{WHERE} GROUP BY s.agent_key) g',
            m,
          ),
          [k.orgId, q.userId, q.deletedAgentKeys],
        ),
        k.query<{ agent_key: string | null; total: number }>(
          SQL(
            `/* pipeshub:conversation.archived_agent_groups */ SELECT s.agent_key, count(*)::int AS total
               FROM conversation.chat_sessions s WHERE @{WHERE} GROUP BY s.agent_key
               ORDER BY max(s.last_activity_at) DESC NULLS LAST, s.agent_key OFFSET $4 LIMIT $5`,
            m,
          ),
          [k.orgId, q.userId, q.deletedAgentKeys, q.agentSkip, q.agentLimit],
        ),
      ]);
      const totalAgents = total.rows[0]?.n ?? 0;
      if (!groups.rows.length) return { groups: [], totalAgents };
      const keys = groups.rows.map((g) => g.agent_key);
      const { rows } = await k.query<SessionRow>(
        SQL(
          `/* pipeshub:conversation.archived_agent_chats */ SELECT * FROM (
              SELECT @{SC}, row_number() OVER (PARTITION BY s.agent_key ORDER BY s.last_activity_at DESC NULLS LAST, s.id DESC) AS rn,
                     COALESCE((SELECT jsonb_agg(jsonb_build_object('userId', mm.user_id, 'accessLevel', mm.access_level, '_id', mm.entry_id) ORDER BY mm.pos)
                       FROM conversation.chat_session_members mm WHERE mm.org_id = $1 AND mm.session_id = s.id), '[]'::jsonb) AS shared_with
                FROM conversation.chat_sessions s WHERE @{WHERE} AND (s.agent_key = ANY($4::text[]) OR ($5::boolean AND s.agent_key IS NULL))) t
            WHERE t.rn <= $6 ORDER BY t.last_activity_at DESC NULLS LAST, t.id DESC`,
          m,
        ),
        [
          k.orgId,
          q.userId,
          q.deletedAgentKeys,
          keys.filter((x) => x !== null),
          keys.includes(null),
          q.chatsPerAgent,
        ],
      );
      const byKey = new Map<string | null, ChatSessionDto[]>();
      for (const r of rows) {
        const list = byKey.get(r.agent_key) ?? [];
        list.push(sessionDto(r, k.orgId, LEAN_SHAPE));
        byKey.set(r.agent_key, list);
      }
      return {
        groups: groups.rows.map((g) => ({
          _id: g.agent_key,
          agentKey: g.agent_key,
          conversations: byKey.get(g.agent_key) ?? [],
          totalCount: g.total,
        })) as unknown as AgentArchiveGroup[],
        totalAgents,
      };
    });
  }

  async findIdsByContent(
    orgId: string,
    escapedSearch: string,
    limit = 10000,
  ): Promise<ContentMatches> {
    const term = unescapeRegex(escapedSearch);
    if (!term) return { ids: [], truncated: false };
    const text = stripNul(term, 'conversation', 'search');
    const cap = Math.min(Math.max(limit, 1), 10000);
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ session_id: string }>(
        `/* pipeshub:conversation.search_content */ SELECT m.session_id FROM conversation.chat_messages m
          WHERE m.org_id = $1 AND m.content ILIKE $2
          GROUP BY m.session_id ORDER BY max(m.created_at) DESC, m.session_id DESC LIMIT $3`,
        [k.orgId, `%${likeEscape(text)}%`, cap + 1],
      );
      return {
        ids: rows.slice(0, cap).map((r) => r.session_id),
        truncated: rows.length > cap,
      };
    });
  }

  async listArtifactMarkerContents(
    sessionId: string,
    orgId: string,
    lastSeq: number,
  ): Promise<string[]> {
    const sid = oidOf(sessionId);
    if (!sid) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ content: string }>(
        `/* pipeshub:conversation.artifact_marker_contents */ SELECT m.content FROM conversation.chat_messages m
          WHERE m.org_id = $1 AND m.session_id = $2::text AND m.seq <= $3 AND strpos(m.content, '::artifact[') > 0
          ORDER BY m.seq`,
        [k.orgId, sid, lastSeq],
      );
      return rows.map((r) => r.content);
    });
  }

  async listVisibleSummaries(
    orgId: string,
    userId: string,
    ids: string[],
    accessibleProjectIds: string[],
  ): Promise<VisibleSummaryRow[]> {
    const want = oidsOf(ids);
    if (!want.length) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{
        id: string;
        title: string | null;
        session_type: 'chat' | 'agent';
        agent_key: string | null;
        project_id: string | null;
        is_archived: boolean;
      }>(
        `/* pipeshub:conversation.visible_summaries */ SELECT s.id, s.title, s.session_type, s.agent_key, s.project_id, s.is_archived FROM conversation.chat_sessions s
          WHERE s.org_id = $1 AND s.id = ANY($2::text[]) AND NOT s.is_deleted
            AND (s.user_id = $3::text
              OR (s.is_shared AND (s.session_type <> 'agent' OR NOT s.is_archived)
                  AND EXISTS (SELECT 1 FROM conversation.chat_session_members w WHERE w.org_id = $1 AND w.session_id = s.id AND w.user_id = $3::text))
              OR (s.project_id = ANY($4::text[]) AND s.project_visibility = 'project' AND NOT s.is_archived))
          ORDER BY s.id`,
        [k.orgId, want, userId, oidsOf(accessibleProjectIds)],
      );
      return rows.map((r) => ({
        _id: r.id,
        ...(r.title === null ? {} : { title: r.title }),
        sessionType: r.session_type,
        ...(r.agent_key === null ? {} : { agentKey: r.agent_key }),
        ...(r.project_id === null ? {} : { projectId: r.project_id }),
        isArchived: r.is_archived,
      }));
    });
  }

  async listByProject(q: {
    orgId: string;
    projectId: string;
    userId: string;
    skip: number;
    limit: number;
  }): Promise<SessionPage> {
    const project = oidOf(q.projectId);
    if (!project) return { rows: [], total: 0 };
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, q.orgId);
      const m = {
        WHERE: `s.org_id = $1 AND s.project_id = $2::text AND NOT s.is_deleted AND (s.user_id = $3::text OR s.project_visibility = 'project')`,
      };
      const [rows, total] = await Promise.all([
        k.query<SessionRow>(
          SQL(
            `/* pipeshub:conversation.list_by_project */ SELECT @{S} FROM conversation.chat_sessions s WHERE @{WHERE}
              ORDER BY s.last_activity_at DESC NULLS LAST, s.id DESC OFFSET $4 LIMIT $5`,
            m,
          ),
          [k.orgId, project, q.userId, q.skip, q.limit],
        ),
        k.query<{ n: number }>(
          SQL(
            '/* pipeshub:conversation.count_by_project */ SELECT count(*)::int AS n FROM conversation.chat_sessions s WHERE @{WHERE}',
            m,
          ),
          [k.orgId, project, q.userId],
        ),
      ]);
      return {
        rows: rows.rows.map((r) => sessionDto(r, k.orgId, LEAN_SHAPE)),
        total: total.rows[0]?.n ?? 0,
      };
    });
  }

  async countByProject(
    orgId: string,
    userId: string,
    projectIds: string[],
  ): Promise<Map<string, number>> {
    const want = oidsOf(projectIds);
    const viewer = oidOf(userId);
    if (!want.length || !viewer) return new Map();
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ project_id: string; n: number }>(
        `/* pipeshub:conversation.count_by_projects */ SELECT s.project_id, count(*)::int AS n FROM conversation.chat_sessions s
          WHERE s.org_id = $1 AND s.project_id = ANY($2::text[]) AND NOT s.is_deleted
            AND (s.user_id = $3 OR s.project_visibility = 'project') GROUP BY s.project_id`,
        [k.orgId, want, viewer],
      );
      return new Map(rows.map((r) => [r.project_id, r.n]));
    });
  }

  async unlinkProject(projectId: string, tx?: Tx): Promise<void> {
    const project = oidOf(projectId);
    if (!project) return;
    await inUnit(tx, { write: true }, (k) =>
      k.query(
        `/* pipeshub:conversation.unlink_project */ UPDATE conversation.chat_sessions SET project_id = NULL, project_visibility = NULL, updated_at = $3
          WHERE org_id = $1 AND project_id = $2::text`,
        [k.orgId, project, new Date()],
      ),
    );
  }

  async unlinkFromProject(
    orgId: string,
    sessionId: string,
    projectId: string,
  ): Promise<void> {
    const session = oidOf(sessionId);
    const project = oidOf(projectId);
    if (!session || !project) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        `/* pipeshub:conversation.unlink_from_project */ UPDATE conversation.chat_sessions SET project_id = NULL, project_visibility = NULL, updated_at = $4
          WHERE org_id = $1 AND id = $2::text AND project_id = $3::text`,
        [k.orgId, session, project, new Date()],
      );
    });
  }

  // ---- sessions: change
  async recordRunState(
    id: string,
    change: RunStateChange,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    const modelPatch: Record<string, unknown> = {};
    for (const field of MODEL_FIELDS) {
      const value: unknown = change.modelInfo?.[field];
      if (value !== undefined && value !== null) modelPatch[field] = value;
    }
    const err = change.appendError;
    return inUnit(tx, { write: true }, (k) => {
      const now = new Date();
      return this.one(
        k,
        SQL(`/* pipeshub:conversation.record_run_state */ UPDATE conversation.chat_sessions s SET
            status = CASE WHEN $3::boolean THEN $4 ELSE s.status END,
            fail_reason = CASE WHEN $5::boolean THEN $6 ELSE s.fail_reason END,
            last_activity_at = CASE WHEN $7::boolean THEN $8::timestamptz ELSE s.last_activity_at END,
            model_info = s.model_info || $9::jsonb,
            conversation_errors = CASE WHEN $10::jsonb IS NULL THEN s.conversation_errors ELSE s.conversation_errors || jsonb_build_array($10::jsonb) END,
            version = s.version + CASE WHEN $10::jsonb IS NULL THEN 0 ELSE 1 END,
            updated_at = $11
          WHERE s.org_id = $1 AND s.id = $2::text RETURNING @{S}`),
        [
          k.orgId,
          oid,
          change.status !== undefined,
          change.status ?? null,
          change.failReason !== undefined,
          change.failReason ?? null,
          change.lastActivityAt !== undefined,
          change.lastActivityAt !== undefined
            ? new Date(change.lastActivityAt)
            : null,
          JSON.stringify(modelPatch),
          err
            ? JSON.stringify(
                stripNul(
                  {
                    _id: newObjectId(),
                    message: err.message,
                    ...(err.errorType !== undefined
                      ? { errorType: err.errorType }
                      : {}),
                    timestamp: now.toISOString(),
                    ...(err.messageId !== undefined
                      ? { messageId: err.messageId }
                      : {}),
                    ...(err.stack !== undefined ? { stack: err.stack } : {}),
                    ...(err.metadata
                      ? { metadata: Object.fromEntries(err.metadata) }
                      : {}),
                  },
                  'conversation',
                  'conversation_errors',
                ),
              )
            : null,
          now,
        ],
        DOC_SHAPE,
      );
    });
  }

  async setTitle(
    id: string,
    title: string,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(tx, { write: true }, (k) =>
      this.one(
        k,
        SQL(`/* pipeshub:conversation.set_title */ UPDATE conversation.chat_sessions s SET title = $3, updated_at = $4
          WHERE s.org_id = $1 AND s.id = $2::text RETURNING @{S}`),
        [k.orgId, oid, stripNul(title, 'conversation', 'title'), new Date()],
        DOC_SHAPE,
      ),
    );
  }

  async setSharing(
    scope: SessionScope,
    id: string,
    patch: {
      isShared?: boolean;
      sharedWith: SessionShareDto[];
      clearShareLink?: boolean;
    },
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(tx, { write: true }, async (k) => {
      const done = await k.query(
        `/* pipeshub:conversation.set_sharing */ UPDATE conversation.chat_sessions s SET
            is_shared = CASE WHEN $4::boolean THEN $5::boolean ELSE s.is_shared END,
            share_link = CASE WHEN $6::boolean THEN NULL ELSE s.share_link END, updated_at = $7
          WHERE s.org_id = $1 AND s.id = $2::text AND s.session_type = $3`,
        [
          k.orgId,
          oid,
          scope.kind,
          patch.isShared !== undefined,
          patch.isShared ?? false,
          !!patch.clearShareLink,
          new Date(),
        ],
      );
      if (done.rowCount !== 1) return null;
      await replaceMembers(k, 'chat', oid, patch.sharedWith);
      return this.one(k, BY_ID, [k.orgId, oid], DOC_SHAPE);
    });
  }

  async setArchived(
    scope: SessionScope,
    id: string,
    archived: { by: string } | null,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(tx, { write: true }, (k) =>
      this.one(
        k,
        SQL(`/* pipeshub:conversation.set_archived */ UPDATE conversation.chat_sessions s SET
            is_archived = $4::boolean, archived_by = $5::text, is_pinned = s.is_pinned AND NOT $4::boolean,
            nulls = CASE WHEN $5::text IS NULL THEN array_append(array_remove(s.nulls, 'archived_by'), 'archived_by') ELSE array_remove(s.nulls, 'archived_by') END,
            last_activity_at = $6, updated_at = $6
          WHERE s.org_id = $1 AND s.id = $2::text AND s.session_type = $3 RETURNING @{S}`),
        [
          k.orgId,
          oid,
          scope.kind,
          archived !== null,
          archived?.by ?? null,
          new Date(),
        ],
        DOC_SHAPE,
      ),
    );
  }

  async discardSession(id: string, orgId: string, tx?: Tx): Promise<void> {
    const oid = oidOf(id);
    if (!oid) return;
    await inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      // Members, messages and their rows follow the session (foreign keys cascade).
      await k.query(
        '/* pipeshub:conversation.discard_session */ DELETE FROM conversation.chat_sessions WHERE org_id = $1 AND id = $2::text',
        [k.orgId, oid],
      );
    });
  }

  async setPinned(
    id: string,
    actor: SessionActor,
    pinned: boolean,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(tx, { write: true }, (k) => {
      assertOrg(k, actor.orgId);
      // No `updated_at`: pinning is not an edit of the chat.
      return this.one(
        k,
        SQL(`/* pipeshub:conversation.set_pinned */ UPDATE conversation.chat_sessions s SET is_pinned = $3::boolean
          WHERE s.org_id = $1 AND s.id = $2::text AND s.session_type = 'chat' AND s.user_id = $4::text AND s.initiator = $4::text
            AND NOT s.is_deleted AND (NOT $3::boolean OR NOT s.is_archived) RETURNING @{S}`),
        [k.orgId, oid, pinned, actor.userId],
        DOC_SHAPE,
      );
    });
  }

  async softDelete(
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<{ session: ChatSessionDto; citationIds: string[] } | null> {
    return inUnit(tx, { write: true }, async (k) => {
      const conversation = await this.findDeletable(id, actor, tx);
      if (!conversation) throw new NotFoundError('Conversation not found');
      const now = new Date();
      const updated = await this.one(
        k,
        SQL(`/* pipeshub:conversation.soft_delete */ UPDATE conversation.chat_sessions s SET is_deleted = true, deleted_by = $3,
            last_activity_at = $4, updated_at = $4 WHERE s.org_id = $1 AND s.id = $2::text AND s.session_type = 'chat' RETURNING @{S}`),
        [k.orgId, conversation._id, actor.userId, now],
        DOC_SHAPE,
      );
      if (!updated)
        throw new InternalServerError('Failed to delete conversation');
      const { rows } = await k.query<{
        citations: Array<{ citationId?: string }>;
      }>(
        `/* pipeshub:conversation.deleted_citations */ SELECT m.citations FROM conversation.chat_messages m
          WHERE m.org_id = $1 AND m.session_id = $2::text ORDER BY m.seq`,
        [k.orgId, conversation._id],
      );
      const citationIds = rows
        .flatMap((r) => r.citations.map((c) => c.citationId))
        .filter((x): x is string => !!x);
      return { session: updated, citationIds };
    });
  }

  async softDeleteAgent(
    id: string,
    userId: string,
  ): Promise<ChatSessionDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: true }, (k) =>
      this.one(
        k,
        SQL(`/* pipeshub:conversation.soft_delete_agent */ UPDATE conversation.chat_sessions s SET is_deleted = true, deleted_by = $3,
            last_activity_at = $4, updated_at = $4 WHERE s.org_id = $1 AND s.id = $2::text RETURNING @{S}`),
        [k.orgId, oid, userId, new Date()],
        DOC_SHAPE,
      ),
    );
  }

  async setProjectLink(
    scope: SessionScope,
    id: string,
    link: {
      projectId: string;
      projectVisibility: 'private' | 'project';
    } | null,
  ): Promise<ChatSessionDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: true }, (k) =>
      this.one(
        k,
        SQL(`/* pipeshub:conversation.set_project_link */ UPDATE conversation.chat_sessions s SET project_id = $5, project_visibility = $6, updated_at = $7
          WHERE s.org_id = $1 AND s.id = $2::text AND s.session_type = $3 AND ($4::text IS NULL OR s.agent_key = $4) RETURNING @{S}`),
        [
          k.orgId,
          oid,
          ...scopeArgs(scope),
          link?.projectId ?? null,
          link?.projectVisibility ?? null,
          new Date(),
        ],
        DOC_SHAPE,
      ),
    );
  }

  async setProjectVisibility(
    scope: SessionScope,
    id: string,
    visibility: 'private' | 'project',
  ): Promise<ChatSessionDto | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: true }, (k) =>
      this.one(
        k,
        SQL(`/* pipeshub:conversation.set_project_visibility */ UPDATE conversation.chat_sessions s SET project_visibility = $5, updated_at = $6
          WHERE s.org_id = $1 AND s.id = $2::text AND s.session_type = $3 AND ($4::text IS NULL OR s.agent_key = $4) RETURNING @{S}`),
        [k.orgId, oid, ...scopeArgs(scope), visibility, new Date()],
        DOC_SHAPE,
      ),
    );
  }

  // ---- run lease
  /**
   * Claims the thread for a new run. Each attempt is its own short unit (the claim and its row lock end with it), so a
   * request waiting for the running turn to settle never holds a lock.
   */
  async reserveRun(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    settleMs: number = ACTIVE_RUN_SETTLE_MS,
  ): Promise<RunReservation | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    const deadline = Date.now() + settleMs;
    const runId = randomUUID();
    for (;;) {
      const now = Date.now();
      const outcome = await inUnit(
        undefined,
        { write: true, fresh: true },
        async (k) => {
          assertOrg(k, actor.orgId);
          const [kind, key] = scopeArgs(scope);
          const { rows } = await k.query<{ previous: string | null }>(
            SQL(`/* pipeshub:conversation.reserve_run */ WITH prev AS (
              SELECT s.status FROM conversation.chat_sessions s
               WHERE s.org_id = $1 AND s.id = $2::text AND NOT s.is_deleted AND s.session_type = $3 AND ($4::text IS NULL OR s.agent_key = $4)
                 AND @{OW} FOR UPDATE OF s)
            UPDATE conversation.chat_sessions s SET status = $6, last_activity_at = $7, active_run_id = $8, updated_at = $7
              FROM prev
             WHERE s.org_id = $1 AND s.id = $2::text
               AND (prev.status IS DISTINCT FROM $6 OR s.last_activity_at IS NULL OR s.last_activity_at <= $9)
            RETURNING prev.status AS previous`),
            [
              k.orgId,
              oid,
              kind,
              key,
              actor.userId,
              LIVE,
              new Date(now),
              runId,
              new Date(now - ACTIVE_RUN_WINDOW_MS),
            ],
          );
          if (rows[0]) {
            const was = rows[0].previous ?? CONVERSATION_STATUS.COMPLETE;
            return {
              sessionId: oid,
              runId,
              previousStatus: was === LIVE ? CONVERSATION_STATUS.FAILED : was,
            };
          }
          const seen = await k.query(
            SQL(`/* pipeshub:conversation.run_visible */ SELECT 1 FROM conversation.chat_sessions s
            WHERE s.org_id = $1 AND s.id = $2::text AND NOT s.is_deleted AND s.session_type = $3 AND ($4::text IS NULL OR s.agent_key = $4)
              AND @{OW}`),
            [k.orgId, oid, kind, key, actor.userId],
          );
          return seen.rowCount ? ('busy' as const) : null;
        },
      );
      if (outcome === null) return null;
      if (outcome !== 'busy') return outcome;
      if (Date.now() >= deadline) {
        throw new ConflictError(
          'A response is still being generated for this conversation. Wait for it to finish, or stop it, then send your message again.',
        );
      }
      await new Promise((resolve) => setTimeout(resolve, ACTIVE_RUN_POLL_MS));
    }
  }

  async releaseRun(r: RunReservation): Promise<void> {
    await inUnit(undefined, { write: true, fresh: true }, (k) =>
      k.query(
        `/* pipeshub:conversation.release_run */ UPDATE conversation.chat_sessions SET status = $3, updated_at = $5
          WHERE org_id = $1 AND id = $2::text AND status = $6 AND active_run_id = $4`,
        [k.orgId, r.sessionId, r.previousStatus, r.runId, new Date(), LIVE],
      ),
    );
  }

  async touchRun(id: string, runId?: string): Promise<void> {
    const oid = oidOf(id);
    if (!oid) return;
    await inUnit(undefined, { write: true, fresh: true }, (k) =>
      k.query(
        `/* pipeshub:conversation.touch_run */ UPDATE conversation.chat_sessions SET last_activity_at = $3
          WHERE org_id = $1 AND id = $2::text AND status = $5 AND ($4::text IS NULL OR active_run_id = $4)`,
        [k.orgId, oid, new Date(), runId ?? null, LIVE],
      ),
    );
  }

  // ---- messages
  /** Allocates the block of `seq` numbers under the session's row lock, then inserts the rows in order. */
  private async insertMessages(
    k: PgKernelTx,
    sessionId: string,
    messages: NewMessage[],
  ): Promise<MessageDto[]> {
    if (!messages.length) return [];
    const alloc = await k.query<{ next_seq: number }>(
      `/* pipeshub:conversation.allocate_seq */ UPDATE conversation.chat_sessions SET next_seq = next_seq + $3
        WHERE org_id = $1 AND id = $2::text RETURNING next_seq`,
      [k.orgId, sessionId, messages.length],
    );
    if (!alloc.rows[0])
      throw new NotFoundError(
        'Chat session not found while allocating message sequence',
      );
    const start = alloc.rows[0].next_seq - messages.length + 1;
    const out: MessageDto[] = [];
    for (const [i, message] of messages.entries()) {
      out.push(await this.insertOne(k, sessionId, start + i, message));
    }
    return out;
  }

  private async insertOne(
    k: PgKernelTx,
    sessionId: string,
    seq: number,
    message: NewMessage,
  ): Promise<MessageDto> {
    const m = stripNul(message, 'conversation', 'message');
    const s = storedMessage(m);
    const now = new Date();
    const { rows } = await k.query<MessageSqlRow>(
      SQL(`/* pipeshub:conversation.insert_message */ INSERT INTO conversation.chat_messages AS m
        (org_id, session_id, seq, id, message_type, content, content_format, status, confidence, citations,
         follow_up_questions, feedback, metadata, model_info, applied_filters, attachments, reference_data, tools, reasoning, parts,
         created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb, $12::jsonb, $13::jsonb, $14::jsonb, $15::jsonb, $16::jsonb,
                $17::jsonb, $18::jsonb, $19::jsonb, $20::jsonb, $21, $22)
        RETURNING @{M}`),
      [
        k.orgId,
        sessionId,
        seq,
        newObjectId(),
        m.messageType,
        // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- callers can omit the text; the document schema defaulted it to ''
        m.content ?? '',
        m.contentFormat ?? 'MARKDOWN',
        m.status ?? null,
        m.confidence ?? null,
        JSON.stringify(s.citations),
        JSON.stringify(s.followUpQuestions),
        JSON.stringify(s.feedback),
        s.metadata ? JSON.stringify(s.metadata) : null,
        JSON.stringify(s.modelInfo),
        JSON.stringify(s.appliedFilters),
        JSON.stringify(s.attachments),
        JSON.stringify(s.referenceData),
        JSON.stringify(s.tools),
        JSON.stringify(s.reasoning),
        JSON.stringify(s.parts),
        m.createdAt ?? now,
        m.updatedAt ?? now,
      ],
    );
    return messageDto(rows[0] as MessageSqlRow, k.orgId).dto;
  }

  async appendMessages(
    sessionId: string,
    orgId: string,
    msgs: NewMessage[],
    tx?: Tx,
  ): Promise<MessageDto[]> {
    if (!msgs.length) return [];
    const oid = oidOf(sessionId);
    if (!oid)
      throw new NotFoundError(
        'Chat session not found while allocating message sequence',
      );
    return inUnit(tx, { write: true }, (k) => {
      assertOrg(k, orgId);
      return this.insertMessages(k, oid, msgs);
    });
  }

  async replaceMessage(
    messageId: string,
    orgId: string,
    msg: NewMessage,
    tx?: Tx,
  ): Promise<MessageDto | null> {
    const oid = oidOf(messageId);
    if (!oid) return null;
    return inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const found = await k.query<{ id: string }>(
        '/* pipeshub:conversation.message_position */ SELECT m.id FROM conversation.chat_messages m WHERE m.org_id = $1 AND m.id = $2::text FOR UPDATE',
        [k.orgId, oid],
      );
      if (!found.rows[0]) return null;
      const stamped = stripNul(msg, 'conversation', 'message');
      const s = storedMessage(stamped);
      const now = new Date();
      const { rows } = await k.query<MessageSqlRow>(
        SQL(`/* pipeshub:conversation.replace_message */ UPDATE conversation.chat_messages m SET
            message_type = $3, content = $4, content_format = $5, status = $6, confidence = $7, citations = $8::jsonb,
            follow_up_questions = $9::jsonb, feedback = $10::jsonb, metadata = $11::jsonb, model_info = $12::jsonb, applied_filters = $13::jsonb,
            attachments = $14::jsonb, reference_data = $15::jsonb, tools = $16::jsonb, reasoning = $17::jsonb, parts = $18::jsonb,
            created_at = $19, updated_at = $20
          WHERE m.org_id = $1 AND m.id = $2::text RETURNING @{M}`),
        [
          k.orgId,
          oid,
          stamped.messageType,
          // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- callers can omit the text; the document schema defaulted it to ''
          stamped.content ?? '',
          stamped.contentFormat ?? 'MARKDOWN',
          stamped.status ?? null,
          stamped.confidence ?? null,
          JSON.stringify(s.citations),
          JSON.stringify(s.followUpQuestions),
          JSON.stringify(s.feedback),
          s.metadata ? JSON.stringify(s.metadata) : null,
          JSON.stringify(s.modelInfo),
          JSON.stringify(s.appliedFilters),
          JSON.stringify(s.attachments),
          JSON.stringify(s.referenceData),
          JSON.stringify(s.tools),
          JSON.stringify(s.reasoning),
          JSON.stringify(s.parts),
          stamped.createdAt ?? now,
          stamped.updatedAt ?? now,
        ],
      );
      return rows[0] ? messageDto(rows[0], k.orgId).dto : null;
    });
  }

  async deleteMessages(
    messageIds: string[],
    orgId: string,
    tx?: Tx,
    sessionId?: string,
  ): Promise<number> {
    const ids = oidsOf(messageIds);
    if (!ids.length) return 0;
    return inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const r = await k.query(
        '/* pipeshub:conversation.delete_messages */ DELETE FROM conversation.chat_messages WHERE org_id = $1 AND id = ANY($2::text[]) AND ($3::text IS NULL OR session_id = $3)',
        [k.orgId, ids, sessionId ? (oidOf(sessionId) ?? sessionId) : null],
      );
      return r.rowCount ?? 0;
    });
  }

  async updateUserQueryContent(
    messageId: string,
    orgId: string,
    sessionId: string,
    content: string,
    attachments?: IChatAttachmentRef[],
    tx?: Tx,
  ): Promise<MessageDto | null> {
    const mid = oidOf(messageId);
    const sid = oidOf(sessionId);
    if (!mid || !sid) return null;
    return inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<MessageSqlRow>(
        SQL(`/* pipeshub:conversation.update_user_query */ UPDATE conversation.chat_messages m SET content = $4,
            attachments = COALESCE($5::jsonb, m.attachments), updated_at = $6
          WHERE m.org_id = $1 AND m.id = $2::text AND m.session_id = $3::text AND m.message_type = 'user_query' RETURNING @{M}`),
        [
          k.orgId,
          mid,
          sid,
          stripNul(content, 'conversation', 'message'),
          attachments
            ? JSON.stringify(
                stripNul(
                  storedAttachments(attachments),
                  'conversation',
                  'message',
                ),
              )
            : null,
          new Date(),
        ],
      );
      return rows[0] ? messageDto(rows[0], k.orgId).dto : null;
    });
  }

  async appendFeedback(
    messageId: string,
    orgId: string,
    feedback: unknown,
    tx?: Tx,
  ): Promise<MessageDto | null> {
    const oid = oidOf(messageId);
    if (!oid) return null;
    return inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const entry = storedFeedback(
        stripNul(feedback, 'conversation', 'feedback') as Record<
          string,
          unknown
        >,
      );
      const { rows } = await k.query<MessageSqlRow>(
        SQL(`/* pipeshub:conversation.append_feedback */ UPDATE conversation.chat_messages m SET feedback = m.feedback || jsonb_build_array($3::jsonb), updated_at = $4
          WHERE m.org_id = $1 AND m.id = $2::text RETURNING @{M}`),
        [k.orgId, oid, JSON.stringify(entry), new Date()],
      );
      return rows[0] ? messageDto(rows[0], k.orgId).dto : null;
    });
  }

  async findMessage(
    sessionId: string,
    orgId: string,
    messageId: string,
    tx?: Tx,
  ): Promise<MessageDto | null> {
    const sid = oidOf(sessionId);
    const mid = oidOf(messageId);
    if (!sid || !mid) return null;
    return inUnit(tx, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<MessageSqlRow>(
        SQL(`/* pipeshub:conversation.find_message */ SELECT @{M} FROM conversation.chat_messages m
          WHERE m.org_id = $1 AND m.session_id = $2::text AND m.id = $3::text`),
        [k.orgId, sid, mid],
      );
      return rows[0] ? messageDto(rows[0], k.orgId).dto : null;
    });
  }

  async listMessages(
    sessionId: string,
    q: {
      orgId: string;
      skip?: number;
      limit?: number;
      withCitations?: boolean;
      order?: 'asc' | 'desc';
    },
    tx?: Tx,
  ): Promise<MessageRow[]> {
    const { skip = 0, limit, withCitations = false, order = 'asc' } = q;
    if (limit !== undefined && limit <= 0) return [];
    const sid = oidOf(sessionId);
    if (!sid) return [];
    return inUnit(tx, { write: false }, async (k) => {
      assertOrg(k, q.orgId);
      const text =
        order === 'desc'
          ? '/* pipeshub:conversation.list_messages_desc */ SELECT @{M} FROM conversation.chat_messages m WHERE m.org_id = $1 AND m.session_id = $2::text ORDER BY m.seq DESC OFFSET $3 LIMIT $4'
          : '/* pipeshub:conversation.list_messages */ SELECT @{M} FROM conversation.chat_messages m WHERE m.org_id = $1 AND m.session_id = $2::text ORDER BY m.seq OFFSET $3 LIMIT $4';
      const { rows } = await k.query<MessageSqlRow>(SQL(text), [
        k.orgId,
        sid,
        skip,
        limit ?? null,
      ]);
      const docs = withCitations
        ? await citationDocs(k, citationIdsOf(rows))
        : undefined;
      return rows.map((r) => messageDto(r, k.orgId, docs));
    });
  }

  async listMessagesOfSessions(
    sessionIds: string[],
    q: { orgId: string; withCitations?: boolean },
  ): Promise<Map<string, MessageRow[]>> {
    const out = new Map<string, MessageRow[]>();
    const ids = oidsOf(sessionIds);
    if (!ids.length) return out;
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, q.orgId);
      const { rows } = await k.query<MessageSqlRow>(
        SQL(`/* pipeshub:conversation.list_messages_of_sessions */ SELECT @{M} FROM conversation.chat_messages m
          WHERE m.org_id = $1 AND m.session_id = ANY($2::text[]) ORDER BY m.seq`),
        [k.orgId, ids],
      );
      const docs = q.withCitations
        ? await citationDocs(k, citationIdsOf(rows))
        : undefined;
      for (const r of rows) {
        const bucket = out.get(r.session_id) ?? [];
        bucket.push(messageDto(r, k.orgId, docs));
        out.set(r.session_id, bucket);
      }
      return out;
    });
  }

  async countMessages(sessionId: string, orgId: string): Promise<number> {
    const sid = oidOf(sessionId);
    if (!sid) return 0;
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ n: number }>(
        '/* pipeshub:conversation.count_messages */ SELECT count(*)::int AS n FROM conversation.chat_messages WHERE org_id = $1 AND session_id = $2::text',
        [k.orgId, sid],
      );
      return rows[0]?.n ?? 0;
    });
  }

  async listAttachmentsAndCitedRecords(
    sessionId: string,
  ): Promise<AttachmentsAndCitedRecords[]> {
    const sid = oidOf(sessionId);
    if (!sid) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      const { rows } = await k.query<MessageSqlRow>(
        SQL(`/* pipeshub:conversation.attachments_and_cited */ SELECT m.id, m.attachments, m.citations FROM conversation.chat_messages m
          WHERE m.org_id = $1 AND m.session_id = $2::text ORDER BY m.seq`),
        [k.orgId, sid],
      );
      const docs = await citationDocs(k, citationIdsOf(rows));
      return rows.map((r) => ({
        _id: r.id,
        attachments: r.attachments,
        citations: r.citations.map((c) => {
          const d = docs.get(String(c.citationId));
          const md = d?.metadata ?? {};
          return {
            ...c,
            citationId: d
              ? {
                  _id: d._id,
                  metadata: {
                    ...(md.recordId !== undefined
                      ? { recordId: md.recordId }
                      : {}),
                    ...(md.connector !== undefined
                      ? { connector: md.connector }
                      : {}),
                  },
                }
              : null,
          };
        }),
      })) as unknown as AttachmentsAndCitedRecords[];
    });
  }

  // ---- citations
  async createCitations(cs: NewCitation[], tx?: Tx): Promise<CitationDto[]> {
    if (!cs.length) return [];
    return inUnit(tx, { write: true }, async (k) => {
      const out: CitationDto[] = [];
      for (const c of cs) out.push(await insertCitation(k, c));
      return out;
    });
  }

  async getCitations(ids: string[], orgId: string): Promise<CitationDto[]> {
    const want = oidsOf(ids);
    if (!want.length) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<CitationSqlRow>(
        SQL(`/* pipeshub:conversation.get_citations */ SELECT @{C} FROM conversation.citations c
          WHERE c.org_id = $1 AND c.id = ANY($2::text[]) ORDER BY c.id`),
        [k.orgId, want],
      );
      return rows.map((r) => citationDto(r, false));
    });
  }
}
