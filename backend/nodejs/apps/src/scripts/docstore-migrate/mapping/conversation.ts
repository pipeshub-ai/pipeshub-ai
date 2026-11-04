import { createHash } from 'node:crypto';
import type {
  ChildMap,
  CollectionMapping,
  FieldMap,
  IgnoredCollection,
} from './types';

type Doc = Record<string, unknown>;

/**
 * Copy order: sessions, messages (their rows need the session row), saved searches, citations (nothing references a citation
 * row by key; the owning organisation is the one inside its metadata).
 */

const lower = (v: unknown): string => String(v).toLowerCase();

/** `sharedWith[]` as the member table holds it: one entry per user (first place and id kept, highest level), ids lower-case. */
function dedupeShared(parent: unknown, list: unknown): Doc[] {
  const seen = new Map<string, Doc>();
  for (const raw of Array.isArray(list) ? (list as unknown[]) : []) {
    const e = raw as Doc | null;
    if (
      !e ||
      typeof e !== 'object' ||
      e.userId === undefined ||
      e.userId === null
    )
      continue;
    const user = lower(e.userId);
    const level = e.accessLevel === 'write' ? 'write' : 'read';
    const had = seen.get(user);
    if (had) {
      if (level === 'write') had.accessLevel = 'write';
      continue;
    }
    seen.set(user, {
      userId: user,
      accessLevel: level,
      _id:
        e._id !== undefined && e._id !== null
          ? lower(e._id)
          : createHash('sha256')
              .update(`${lower(parent)}:${user}`)
              .digest('hex')
              .slice(0, 24),
    });
  }
  return [...seen.values()];
}

const sharedTransform = {
  name: 'sharedWith: one entry per user (highest level), entry id derived when missing',
  apply(doc: Doc): boolean {
    if (!Array.isArray(doc.sharedWith)) return false;
    const before = doc.sharedWith.length;
    doc.sharedWith = dedupeShared(doc._id, doc.sharedWith);
    return (doc.sharedWith as unknown[]).length !== before;
  },
};

const MEMBER_FIELDS: FieldMap[] = [
  { col: 'user_id', path: 'userId', type: 'oid' },
  { col: 'access_level', path: 'accessLevel', type: 'text' },
  { col: 'entry_id', path: '_id', type: 'oid' },
];

const sharedChild = (table: string, parentCol: string): ChildMap => ({
  table,
  parentCol,
  path: 'sharedWith',
  orderCol: 'pos',
  fields: MEMBER_FIELDS,
  derived: ['org_id'],
});

const jsonArray = (col: string, path: string): FieldMap => ({
  col,
  path,
  type: 'json',
  readAbsentAs: [],
});

export const chatSessions: CollectionMapping = {
  collection: 'chatSessions',
  repository: 'conversation',
  table: 'conversation.chat_sessions',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  nullsCol: 'nulls',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    {
      col: 'session_type',
      path: 'sessionType',
      type: 'text',
      readAbsentAs: 'chat',
    },
    { col: 'user_id', path: 'userId', type: 'oid' },
    { col: 'initiator', path: 'initiator', type: 'oid' },
    { col: 'title', path: 'title', type: 'text' },
    { col: 'is_shared', path: 'isShared', type: 'bool', readAbsentAs: false },
    { col: 'share_link', path: 'shareLink', type: 'text' },
    { col: 'is_deleted', path: 'isDeleted', type: 'bool', readAbsentAs: false },
    { col: 'deleted_by', path: 'deletedBy', type: 'oid' },
    {
      col: 'is_archived',
      path: 'isArchived',
      type: 'bool',
      readAbsentAs: false,
    },
    { col: 'archived_by', path: 'archivedBy', type: 'oid', presence: 'listed' },
    // the application stores the pin only while set; a stored false reads as unset (transform below)
    { col: 'is_pinned', path: 'isPinned', type: 'bool', absentWhen: false },
    {
      col: 'is_temporary',
      path: 'isTemporary',
      type: 'bool',
      readAbsentAs: false,
    },
    { col: 'expires_at', path: 'expiresAt', type: 'date' },
    { col: 'last_activity_at', path: 'lastActivityAt', type: 'epoch_ms' },
    { col: 'status', path: 'status', type: 'text' },
    { col: 'fail_reason', path: 'failReason', type: 'text' },
    { col: 'active_run_id', path: 'activeRunId', type: 'text' },
    { col: 'next_seq', path: 'nextSeq', type: 'int', readAbsentAs: 0 },
    {
      col: 'model_info',
      path: 'modelInfo',
      type: 'json',
      readAbsentAs: { chatMode: 'quick' },
    },
    jsonArray('conversation_errors', 'conversationErrors'),
    { col: 'metadata', path: 'metadata', type: 'json' },
    { col: 'agent_key', path: 'agentKey', type: 'text' },
    { col: 'conversation_source', path: 'conversationSource', type: 'text' },
    { col: 'compacted_summary', path: 'compactedSummary', type: 'text' },
    {
      col: 'compacted_at_turn_index',
      path: 'compactedAtTurnIndex',
      type: 'int',
    },
    {
      col: 'compacted_at_timestamp',
      path: 'compactedAtTimestamp',
      type: 'bigint',
    },
    { col: 'project_id', path: 'projectId', type: 'oid' },
    { col: 'project_visibility', path: 'projectVisibility', type: 'text' },
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  children: [sharedChild('conversation.chat_session_members', 'session_id')],
  derived: [],
  dropped: [
    {
      path: 'compareOf',
      reason:
        'Compare models is off on the PostgreSQL document store; branches are not copied yet',
    },
    {
      path: 'compareFromMessageId',
      reason:
        'Compare models is off on the PostgreSQL document store; branches are not copied yet',
    },
  ],
  transforms: [
    sharedTransform,
    {
      name: 'isPinned: false read as unset',
      apply(doc: Doc): boolean {
        if (doc.isPinned !== false) return false;
        delete doc.isPinned;
        return true;
      },
    },
  ],
};

export const chatSessionMessages: CollectionMapping = {
  collection: 'chatSessionMessages',
  repository: 'conversation',
  table: 'conversation.chat_messages',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    { col: 'session_id', path: 'sessionId', type: 'oid' },
    { col: 'seq', path: 'seq', type: 'int' },
    { col: 'message_type', path: 'messageType', type: 'text' },
    { col: 'content', path: 'content', type: 'text', readAbsentAs: '' },
    { col: 'content_format', path: 'contentFormat', type: 'text' },
    { col: 'status', path: 'status', type: 'text' },
    { col: 'confidence', path: 'confidence', type: 'text' },
    jsonArray('citations', 'citations'),
    jsonArray('follow_up_questions', 'followUpQuestions'),
    jsonArray('feedback', 'feedback'),
    { col: 'metadata', path: 'metadata', type: 'json' },
    { col: 'model_info', path: 'modelInfo', type: 'json' },
    { col: 'applied_filters', path: 'appliedFilters', type: 'json' },
    jsonArray('attachments', 'attachments'),
    jsonArray('reference_data', 'referenceData'),
    jsonArray('tools', 'tools'),
    jsonArray('reasoning', 'reasoning'),
    jsonArray('parts', 'parts'),
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  derived: [],
  dropped: [
    {
      path: 'authorUserId',
      reason:
        'messages keep no author column; a chat is shared with its readers whole',
    },
    {
      path: 'sourceRecordIds',
      reason:
        'messages keep no source-record list; the citations hold the records',
    },
  ],
};

export const searches: CollectionMapping = {
  collection: 'searches',
  repository: 'semantic-search',
  table: 'conversation.semantic_searches',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  nullsCol: 'nulls',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    { col: 'query', path: 'query', type: 'text' },
    { col: 'result_limit', path: 'limit', type: 'int' },
    { col: 'user_id', path: 'userId', type: 'oid' },
    { col: 'citation_ids', path: 'citationIds', type: 'oid_array' },
    { col: 'records', path: 'records', type: 'json', readAbsentAs: {} },
    { col: 'is_shared', path: 'isShared', type: 'bool', readAbsentAs: false },
    { col: 'share_link', path: 'shareLink', type: 'text' },
    {
      col: 'is_archived',
      path: 'isArchived',
      type: 'bool',
      readAbsentAs: false,
    },
    { col: 'archived_by', path: 'archivedBy', type: 'oid', presence: 'listed' },
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  children: [sharedChild('conversation.semantic_search_members', 'search_id')],
  derived: [],
  transforms: [
    sharedTransform,
    {
      name: 'absent citationIds read as []',
      apply(doc: Doc): boolean {
        if (doc.citationIds !== undefined) return false;
        doc.citationIds = [];
        return true;
      },
    },
  ],
};

export const citations: CollectionMapping = {
  collection: 'citations',
  repository: 'conversation',
  table: 'conversation.citations',
  idCol: 'id',
  // a citation has no organisation of its own: the one inside its metadata (a string) owns the row
  orgPath: 'metadata.orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    { col: 'content', path: 'content', type: 'text' },
    { col: 'chunk_index', path: 'chunkIndex', type: 'int' },
    { col: 'citation_type', path: 'citationType', type: 'text' },
    { col: 'metadata', path: 'metadata', type: 'json' },
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  // org_id, record_id and record_name are copies of metadata.orgId, .recordId and .recordName (checked by the invariants below)
  derived: ['org_id', 'record_id', 'record_name'],
};

export const CONVERSATION_MAPPINGS: CollectionMapping[] = [
  chatSessions,
  chatSessionMessages,
  searches,
  citations,
];

export const CONVERSATION_IGNORED: IgnoredCollection[] = [
  {
    collection: 'conversations',
    reason:
      'boot migration chat_sessions must have run; preflight is red while they hold documents',
  },
  {
    collection: 'agentconversations',
    reason:
      'boot migration chat_sessions must have run; preflight is red while they hold documents',
  },
];

/**
 * Invariants on the copied rows: each query returns the ids that break the rule (none is the pass). Unlike the mappers they
 * look at the rows as stored, so a wrong organisation stamp that row policies do not stop is found here.
 */
export const CONVERSATION_INVARIANTS: Array<{ name: string; sql: string }> = [
  {
    name: 'a citation belongs to the organisation of the message that cites it',
    // expanded once, then hash-joined: a containment test per citation would scan every message for every citation
    sql: `SELECT c.id FROM conversation.citations c
            JOIN (SELECT m.org_id, e->>'citationId' AS cid FROM conversation.chat_messages m, jsonb_array_elements(m.citations) e
                   WHERE jsonb_typeof(m.citations) = 'array') x ON x.cid = c.id WHERE x.org_id <> c.org_id`,
  },
  {
    name: 'a citation belongs to the organisation of the saved search that lists it',
    sql: `SELECT c.id FROM conversation.citations c JOIN (SELECT s.org_id, unnest(s.citation_ids) AS cid FROM conversation.semantic_searches s) x ON x.cid = c.id
            WHERE x.org_id <> c.org_id`,
  },
  {
    name: 'a citation row repeats its metadata (organisation, record id and name)',
    sql: `SELECT c.id FROM conversation.citations c
            WHERE c.org_id <> c.metadata->>'orgId' OR c.record_id <> c.metadata->>'recordId' OR c.record_name IS DISTINCT FROM c.metadata->>'recordName'`,
  },
  {
    name: 'no message is beyond the session allocator (next_seq is the last position handed out)',
    sql: `SELECT s.id FROM conversation.chat_sessions s WHERE EXISTS (SELECT 1 FROM conversation.chat_messages m WHERE m.org_id = s.org_id AND m.session_id = s.id AND m.seq > s.next_seq)`,
  },
];

citations.invariants = CONVERSATION_INVARIANTS;
