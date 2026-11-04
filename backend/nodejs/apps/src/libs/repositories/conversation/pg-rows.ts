/**
 * Row shapes of the conversation tables and the pure conversions between them and the DTOs the repository returns.
 * The DTOs are the ones the document store served (same key set, same JSON types); a column that is NULL is an
 * omitted key unless the row lists its field in `nulls` (a field stored as an explicit null).
 */
import {
  shapeAppliedFilters,
  shapeCitationMetadata,
  shapeCitationRef,
  shapeFeedback,
  shapeFollowUp,
  shapeMessageMetadata,
  shapeReasoning,
  shapeReference,
  shapeTool,
  shapeAttachment,
} from './pg-schema';
import type {
  ChatSessionDto,
  CitationDto,
  MessageDto,
  MessageRow,
  NewCitation,
  NewMessage,
  SessionErrorDto,
} from './types';

export interface SessionRow {
  id: string;
  org_id?: string;
  session_type: 'chat' | 'agent';
  user_id: string;
  initiator: string;
  title: string | null;
  is_shared: boolean;
  share_link: string | null;
  is_deleted: boolean;
  deleted_by: string | null;
  is_archived: boolean;
  archived_by: string | null;
  last_activity_at: Date | null;
  status: string | null;
  fail_reason: string | null;
  next_seq?: number;
  model_info: Record<string, unknown>;
  conversation_errors: Array<Record<string, unknown>>;
  metadata: Record<string, unknown> | null;
  agent_key: string | null;
  conversation_source: string | null;
  compacted_summary: string | null;
  compacted_at_turn_index: number | null;
  compacted_at_timestamp: string | null;
  project_id: string | null;
  project_visibility: string | null;
  version: number;
  nulls: string[];
  is_pinned: boolean;
  created_at: Date;
  updated_at: Date;
  shared_with: Array<{ userId: string; accessLevel: 'read' | 'write' }>;
}

export interface MessageSqlRow {
  id: string;
  session_id: string;
  seq: number;
  message_type: MessageDto['messageType'];
  content: string;
  content_format: string | null;
  status: string | null;
  confidence: string | null;
  citations: Array<Record<string, unknown>>;
  follow_up_questions: unknown[];
  feedback: unknown[];
  metadata: Record<string, unknown> | null;
  model_info: Record<string, unknown> | null;
  applied_filters: Record<string, unknown> | null;
  attachments: unknown[];
  reference_data: unknown[];
  tools: unknown[];
  reasoning: unknown[];
  parts: unknown[];
  created_at: Date;
  updated_at: Date;
}

export interface CitationSqlRow {
  id: string;
  content: string;
  chunk_index: number;
  citation_type: string;
  metadata: Record<string, unknown>;
  version: number;
  created_at: Date;
  updated_at: Date;
}

const put = (o: Record<string, unknown>, k: string, v: unknown): void => {
  if (v !== null && v !== undefined) o[k] = v;
};

const asMap = (
  o: Record<string, unknown> | null | undefined,
): Map<string, unknown> | undefined =>
  o ? new Map(Object.entries(o)) : undefined;

interface SessionShape {
  /** A document read (`toObject()`): maps stay maps, `__v` is present. A lean read has plain objects. */
  doc: boolean;
  /** Reads that selected `-__v` leave it out. */
  version: boolean;
  /** Only a freshly created session carries its internal fields. */
  withInternals?: boolean;
  /** Shared-with-me lists drop the member list. */
  omitSharedWith?: boolean;
}

function sessionErrors(
  list: Array<Record<string, unknown>>,
  doc: boolean,
): SessionErrorDto[] {
  return list.map((e) => {
    const out: Record<string, unknown> = { message: e.message };
    put(out, '_id', e._id);
    put(out, 'errorType', e.errorType);
    out.timestamp = new Date(String(e.timestamp));
    put(out, 'messageId', e.messageId);
    put(out, 'stack', e.stack);
    if (e.metadata)
      out.metadata = doc
        ? asMap(e.metadata as Record<string, unknown>)
        : e.metadata;
    return out as unknown as SessionErrorDto;
  });
}

export function sessionDto(
  r: SessionRow,
  orgId: string,
  s: SessionShape,
): ChatSessionDto {
  const o: Record<string, unknown> = {
    _id: r.id,
    orgId,
    userId: r.user_id,
    initiator: r.initiator,
    isShared: r.is_shared,
    isDeleted: r.is_deleted,
    isArchived: r.is_archived,
    modelInfo: r.model_info,
    conversationErrors: sessionErrors(r.conversation_errors, s.doc),
  };
  put(o, 'title', r.title);
  put(o, 'shareLink', r.share_link);
  if (!s.omitSharedWith) o.sharedWith = r.shared_with;
  // A pin belongs to its owner, so a list of chats shared with the reader never shows it.
  if (r.is_pinned && !s.omitSharedWith) o.isPinned = true;
  put(o, 'deletedBy', r.deleted_by);
  if (r.archived_by !== null) o.archivedBy = r.archived_by;
  else if (r.nulls.includes('archived_by')) o.archivedBy = null;
  if (r.last_activity_at) o.lastActivityAt = r.last_activity_at.getTime();
  put(o, 'status', r.status);
  put(o, 'failReason', r.fail_reason);
  if (r.metadata) o.metadata = s.doc ? asMap(r.metadata) : r.metadata;
  put(o, 'agentKey', r.agent_key);
  put(o, 'conversationSource', r.conversation_source);
  put(o, 'compactedSummary', r.compacted_summary);
  put(o, 'compactedAtTurnIndex', r.compacted_at_turn_index);
  if (r.compacted_at_timestamp !== null)
    o.compactedAtTimestamp = Number(r.compacted_at_timestamp);
  put(o, 'projectId', r.project_id);
  put(o, 'projectVisibility', r.project_visibility);
  o.createdAt = r.created_at;
  o.updatedAt = r.updated_at;
  if (s.version) o.__v = r.version;
  if (s.withInternals) {
    o.sessionType = r.session_type;
    o.nextSeq = r.next_seq ?? 0;
  }
  return o as unknown as ChatSessionDto;
}

/** The fields a new session is created with, as the document store would have stored them. */
export const MODEL_FIELDS = [
  'modelKey',
  'modelName',
  'modelProvider',
  'chatMode',
  'modelFriendlyName',
  'reasoningEffort',
] as const;

/** An answer also keeps the note the AI service added about the model that wrote it. */
export const MESSAGE_MODEL_FIELDS = [...MODEL_FIELDS, 'modelNote'] as const;

export function storedModelInfo(
  m: Record<string, unknown> | undefined,
  fields: readonly string[] = MODEL_FIELDS,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields)
    if (m?.[f] !== undefined && m[f] !== null) out[f] = m[f];
  out.chatMode ??= 'quick';
  return out;
}

const clean = <T>(v: T): T => JSON.parse(JSON.stringify(v ?? null)) as T;

/** A feedback entry as the document schema stored it: unknown fields dropped, defaults filled. */
export const storedFeedback = (
  f: Record<string, unknown>,
): Record<string, unknown> => clean(shapeFeedback(f));

export interface StoredMessage {
  citations: unknown[];
  followUpQuestions: unknown[];
  feedback: unknown[];
  metadata: Record<string, unknown> | null;
  modelInfo: Record<string, unknown>;
  appliedFilters: Record<string, unknown>;
  attachments: unknown[];
  referenceData: unknown[];
  tools: unknown[];
  reasoning: unknown[];
  parts: unknown[];
}

const each = (
  v: readonly unknown[] | undefined,
  shape: (x: unknown) => unknown,
): unknown[] => (v ?? []).map(shape).map((x) => clean(x));

/** Attachment refs as the document schema stored them. */
export const storedAttachments = (
  attachments: readonly unknown[] | undefined,
): unknown[] => each(attachments, shapeAttachment);

/** Message fields as the document schema stored them (unknown fields dropped, defaults filled), as the columns hold them. */
export function storedMessage(m: NewMessage): StoredMessage {
  const referenceData = each(m.referenceData, shapeReference);
  const tools = each(m.tools, shapeTool);
  const reasoning = each(m.reasoning, shapeReasoning);
  const parts = clean(m.parts ?? []);
  return {
    citations: each(m.citations, shapeCitationRef),
    followUpQuestions: each(m.followUpQuestions, shapeFollowUp),
    feedback: each(m.feedback, shapeFeedback),
    metadata: clean(shapeMessageMetadata(m.metadata)),
    modelInfo: storedModelInfo(
      m.modelInfo as unknown as Record<string, unknown> | undefined,
      MESSAGE_MODEL_FIELDS,
    ),
    appliedFilters: clean(shapeAppliedFilters(m.appliedFilters)),
    attachments: each(m.attachments, shapeAttachment),
    referenceData,
    tools,
    reasoning,
    parts,
  };
}

export function messageDto(
  r: MessageSqlRow,
  orgId: string,
  citationDocs?: Map<string, CitationDto | undefined>,
): MessageRow {
  const o: Record<string, unknown> = {
    _id: r.id,
    sessionId: r.session_id,
    orgId,
    seq: r.seq,
    messageType: r.message_type,
    content: r.content,
    citations: r.citations.map((c) => {
      if (!citationDocs) return c;
      const doc = citationDocs.get(String(c.citationId));
      return { ...c, citationId: doc ?? null };
    }),
    followUpQuestions: r.follow_up_questions,
    feedback: r.feedback,
    modelInfo: r.model_info,
    appliedFilters: r.applied_filters,
    attachments: r.attachments,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
  put(o, 'contentFormat', r.content_format);
  put(o, 'status', r.status);
  put(o, 'confidence', r.confidence);
  put(o, 'metadata', r.metadata);
  o.referenceData = r.reference_data;
  o.tools = r.tools;
  o.reasoning = r.reasoning;
  o.parts = r.parts;
  return { dto: o as unknown as MessageDto };
}

export function citationDto(
  r: CitationSqlRow,
  withVersion: boolean,
): CitationDto {
  const o: Record<string, unknown> = {
    _id: r.id,
    content: r.content,
    chunkIndex: r.chunk_index,
    citationType: r.citation_type,
    metadata: r.metadata,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
  if (withVersion) o.__v = r.version;
  return o as unknown as CitationDto;
}

/** Citation metadata as the document schema stored it: its fields only, defaults filled, an id on each bounding box. */
export const storedCitationMetadata = (
  c: NewCitation,
): Record<string, unknown> => clean(shapeCitationMetadata(c.metadata));
