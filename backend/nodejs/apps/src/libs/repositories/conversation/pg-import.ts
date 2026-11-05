/** Mongo documents of `chatSessions`, `chatSessionMessages` and `citations` as rows of the conversation tables. */
import type { Tx as KernelTx } from '../../db';
import {
  ImportPlan,
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  msToIso,
  sha256Hex,
  writePlan,
} from '../import';

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const obj = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;

export interface MemberRow {
  user_id: string;
  access_level: string;
  pos: number;
  entry_id: string;
}

/**
 * `sharedWith[]` as member rows. The table holds one row per user: the first entry's place and id are kept, with the highest
 * level any entry gave. An entry without an `_id` gets one derived from the parent and the user, so a second run agrees.
 */
export function memberRows(parentId: string, shared: unknown): MemberRow[] {
  const byUser = new Map<string, MemberRow>();
  for (const e of arr(shared)) {
    const m = obj(e);
    const user = idText(m?.userId);
    if (!m || !user) continue;
    const level = m.accessLevel === 'write' ? 'write' : 'read';
    const had = byUser.get(user);
    if (had) {
      if (level === 'write') had.access_level = 'write';
      continue;
    }
    byUser.set(user, {
      user_id: user,
      access_level: level,
      pos: byUser.size,
      entry_id: idText(m._id) ?? sha256Hex(`${parentId}:${user}`).slice(0, 24),
    });
  }
  return [...byUser.values()];
}

/** Names of the fields stored as an explicit null that the tables list in `nulls`. */
export const nullsOf = (d: NeutralDoc): string[] =>
  d.archivedBy === null ? ['archived_by'] : [];

export function sessionRow(d: NeutralDoc): Row | null {
  const id = idText(d._id);
  const org = idText(d.orgId);
  const user = idText(d.userId);
  const initiator = idText(d.initiator);
  if (!id || !org || !user || !initiator) return null;
  const r: Row = {
    org_id: org,
    id,
    session_type: d.sessionType === 'agent' ? 'agent' : 'chat',
    user_id: user,
    initiator,
    title: str(d.title),
    is_shared: d.isShared ?? false,
    share_link: str(d.shareLink),
    is_deleted: d.isDeleted ?? false,
    deleted_by: idText(d.deletedBy),
    is_archived: d.isArchived ?? false,
    archived_by: idText(d.archivedBy),
    is_pinned: d.isPinned === true,
    last_activity_at: msToIso(d.lastActivityAt),
    status: str(d.status),
    fail_reason: str(d.failReason),
    active_run_id: str(d.activeRunId),
    next_seq: typeof d.nextSeq === 'number' ? d.nextSeq : 0,
    conversation_errors: arr(d.conversationErrors),
    metadata: obj(d.metadata),
    agent_key: str(d.agentKey),
    conversation_source: str(d.conversationSource),
    compacted_summary: str(d.compactedSummary),
    compacted_at_turn_index:
      typeof d.compactedAtTurnIndex === 'number'
        ? d.compactedAtTurnIndex
        : null,
    compacted_at_timestamp:
      typeof d.compactedAtTimestamp === 'number'
        ? d.compactedAtTimestamp
        : null,
    project_id: idText(d.projectId),
    project_visibility: str(d.projectVisibility),
    version: typeof d.__v === 'number' ? d.__v : 0,
    nulls: nullsOf(d),
    created_at: d.createdAt,
    updated_at: d.updatedAt ?? d.createdAt,
  };
  // a session stored without model info takes the table's default
  if (obj(d.modelInfo)) r.model_info = d.modelInfo;
  return r;
}

/** The sessions the messages belong to (keyed `org/id`) that have a row: a message needs its session's row. */
async function sessionsOf(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<Set<string>> {
  const orgs: string[] = [];
  const ids: string[] = [];
  for (const d of docs) {
    const o = idText(d.orgId);
    const s = idText(d.sessionId);
    if (o && s) {
      orgs.push(o);
      ids.push(s);
    }
  }
  const out = new Set<string>();
  if (!ids.length) return out;
  const { rows } = await k.query<{ org_id: string; id: string }>(
    '/* pipeshub:import.message_sessions */ SELECT s.org_id, s.id FROM conversation.chat_sessions s JOIN unnest($1::text[], $2::text[]) AS w(org_id, id) ON s.org_id = w.org_id AND s.id = w.id',
    [orgs, ids],
  );
  for (const r of rows) out.add(`${r.org_id}/${r.id}`);
  return out;
}

export function messageRow(d: NeutralDoc): Row | null {
  const id = idText(d._id);
  const org = idText(d.orgId);
  const session = idText(d.sessionId);
  if (
    !id ||
    !org ||
    !session ||
    typeof d.seq !== 'number' ||
    typeof d.messageType !== 'string'
  )
    return null;
  return {
    org_id: org,
    session_id: session,
    seq: d.seq,
    id,
    message_type: d.messageType,
    content: typeof d.content === 'string' ? d.content : '',
    content_format: str(d.contentFormat),
    status: str(d.status),
    confidence: str(d.confidence),
    citations: arr(d.citations),
    follow_up_questions: arr(d.followUpQuestions),
    feedback: arr(d.feedback),
    metadata: obj(d.metadata),
    model_info: obj(d.modelInfo),
    applied_filters: obj(d.appliedFilters),
    attachments: arr(d.attachments),
    reference_data: arr(d.referenceData),
    tools: arr(d.tools),
    reasoning: arr(d.reasoning),
    parts: arr(d.parts),
    created_at: d.createdAt,
    updated_at: d.updatedAt ?? d.createdAt,
  };
}

/** The organisation of a citation is the one inside its metadata (the document has no other). */
export function citationRow(d: NeutralDoc): Row | null {
  const id = idText(d._id);
  const meta = obj(d.metadata);
  const org = idText(meta?.orgId);
  if (!id || !meta || !org || typeof meta.recordId !== 'string') return null;
  return {
    org_id: org,
    id,
    content: str(d.content) ?? '',
    chunk_index: typeof d.chunkIndex === 'number' ? d.chunkIndex : 0,
    citation_type: str(d.citationType) ?? '',
    record_id: meta.recordId,
    record_name: str(meta.recordName),
    metadata: meta,
    version: typeof d.__v === 'number' ? d.__v : 0,
    created_at: d.createdAt,
    updated_at: d.updatedAt ?? d.createdAt,
  };
}

export async function importConversationDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const plan: ImportPlan = [];
  const skip = (d: NeutralDoc, reason: string): void =>
    void stats.skipped.push({ id: String(d._id), reason });
  if (collection === 'chatSessions') {
    const sessions: Row[] = [];
    const members: Row[] = [];
    for (const d of docs) {
      const r = sessionRow(d);
      if (!r) {
        skip(
          d,
          'chatSessions document without a usable _id, orgId, userId or initiator',
        );
        continue;
      }
      sessions.push(r);
      for (const m of memberRows(r.id as string, d.sharedWith))
        members.push({ org_id: r.org_id, session_id: r.id, ...m });
    }
    plan.push(
      { table: 'conversation.chat_sessions', rows: sessions },
      { table: 'conversation.chat_session_members', rows: members },
    );
  } else if (collection === 'chatSessionMessages') {
    const sessions = await sessionsOf(k, docs);
    const rows: Row[] = [];
    for (const d of docs) {
      const key = `${String(idText(d.orgId))}/${String(idText(d.sessionId))}`;
      if (!sessions.has(key)) {
        skip(d, 'message without a session in its organisation');
        continue;
      }
      const r = messageRow(d);
      if (r) rows.push(r);
      else
        skip(
          d,
          'chatSessionMessages document without a usable _id, orgId, sessionId, seq or messageType',
        );
    }
    plan.push({ table: 'conversation.chat_messages', rows });
  } else if (collection === 'citations') {
    const rows: Row[] = [];
    for (const d of docs) {
      const r = citationRow(d);
      if (r) rows.push(r);
      else
        skip(
          d,
          'citation without a usable metadata.orgId or metadata.recordId',
        );
    }
    plan.push({ table: 'conversation.citations', rows });
  } else
    throw new Error(
      `the conversation repository does not import '${collection}'`,
    );
  await writePlan(k, plan, stats);
  return stats;
}
