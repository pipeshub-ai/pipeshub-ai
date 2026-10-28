/**
 * Conversation repository: chat and agent sessions, their messages and the
 * citations messages point at.
 *
 * Rows leave the repository as plain objects with every id as a string and keep
 * the shape the Mongo query returned (`.lean()` rows stay lean, `toObject()`
 * results stay `toObject()` results), so a response built from them serialises
 * exactly as before. No driver type appears here: no ObjectId, ClientSession,
 * FilterQuery or Document.
 *
 * Methods are use cases, not filters. Who may do what (owner, shared member, project-visible) is decided inside the implementation.
 */
import type {
  IAIModel,
  IAppliedFilters,
  IChatAttachmentRef,
  IFeedback,
  IFollowUpQuestion,
  IMessage,
  IMessagePart,
  IReasoningTurn,
  IReferenceDataItem,
  IToolCallItem,
} from '../../../modules/enterprise_search/types/conversation.interfaces';
import type { Tx } from '../uow';

/** Plain chats and agent chats share one collection; an agent scope may name the agent. */
export type SessionScope =
  { kind: 'chat' } | { kind: 'agent'; agentKey?: string };

export type AccessLevel = 'read' | 'write';

export interface SessionShareDto {
  _id?: string;
  userId: string;
  accessLevel: AccessLevel;
}

export interface SessionErrorDto {
  message: string;
  errorType?: string;
  timestamp?: Date;
  messageId?: string;
  stack?: string;
  metadata?: Map<string, unknown> | Record<string, unknown>;
}

/** A session row. `sessionType`, `nextSeq` and `activeRunId` are internal and never read through here except where noted. */
export interface ChatSessionDto {
  _id: string;
  orgId: string;
  userId: string;
  initiator: string;
  title?: string;
  isShared?: boolean;
  shareLink?: string;
  sharedWith?: SessionShareDto[];
  isDeleted?: boolean;
  deletedBy?: string;
  isArchived?: boolean;
  archivedBy?: string;
  /** The owner's pin: `true` when pinned, absent otherwise (never `false`). */
  isPinned?: boolean;
  lastActivityAt?: number;
  status?: string;
  failReason?: string;
  modelInfo?: Partial<IAIModel>;
  conversationErrors?: SessionErrorDto[];
  metadata?: Map<string, unknown> | Record<string, unknown>;
  agentKey?: string;
  conversationSource?: 'agent_chat';
  compactedSummary?: string;
  compactedAtTurnIndex?: number;
  compactedAtTimestamp?: number;
  projectId?: string;
  projectVisibility?: 'private' | 'project';
  createdAt?: Date;
  updatedAt?: Date;
  __v?: number;
  /** Present on a freshly created row only (schema defaults), stripped before any response. */
  sessionType?: 'chat' | 'agent';
  nextSeq?: number;
}

/** Everything a new session is created with. */
export interface NewSession {
  /** Allocated by the caller when something outside the store (a fork's artifacts) is filed under it first. */
  id?: string;
  /** Free-form notes kept on the session (a fork records where it was copied from). */
  metadata?: Record<string, unknown>;
  orgId: string;
  userId: string;
  initiator: string;
  title?: string;
  lastActivityAt: number;
  status: string;
  modelInfo?: Partial<IAIModel>;
  sessionType: 'chat' | 'agent';
  agentKey?: string;
  conversationSource?: 'agent_chat';
  projectId?: string;
  projectVisibility?: 'private' | 'project';
}

export interface MessageCitationDto {
  citationId?: string;
  relevanceScore?: number;
  excerpt?: string;
  context?: string;
  /** Only when citations were populated: the citation row. */
  citationData?: CitationDto;
}

/** A message row as stored (`.lean()` shape). */
export interface MessageDto {
  _id: string;
  sessionId: string;
  orgId: string;
  seq: number;
  messageType: IMessage['messageType'];
  content: string;
  contentFormat?: IMessage['contentFormat'];
  status?: 'stopped';
  citations?: MessageCitationDto[];
  confidence?: string;
  followUpQuestions?: IFollowUpQuestion[];
  feedback?: IFeedback[];
  metadata?: IMessage['metadata'];
  modelInfo?: IAIModel;
  appliedFilters?: IAppliedFilters;
  attachments?: IChatAttachmentRef[];
  referenceData?: IReferenceDataItem[];
  tools?: IToolCallItem[];
  reasoning?: IReasoningTurn[];
  parts?: IMessagePart[];
  createdAt?: Date;
  updatedAt?: Date;
}

/** A message as the store returns it. */
export interface MessageRow {
  dto: MessageDto;
}

export interface NewMessage extends Omit<IMessage, 'citations'> {
  citations?: Array<{
    citationId?: string;
    relevanceScore?: number;
    excerpt?: string;
    context?: string;
  }>;
}

export interface CitationDto {
  _id: string;
  content: string;
  chunkIndex: number;
  citationType: string;
  metadata: Record<string, unknown>;
  createdAt?: Date;
  updatedAt?: Date;
  __v?: number;
}

export interface NewCitation {
  content: string;
  chunkIndex: number;
  citationType: string;
  metadata: Record<string, unknown>;
}

/** Caller identity for a session lookup. The org is explicit until the ambient org context is safe in stream callbacks. */
export interface SessionActor {
  orgId: string;
  userId: string;
}

/** The thread's current run, so release and heartbeat only act on their own. */
export interface RunReservation {
  sessionId: string;
  runId: string;
  previousStatus: string;
}

export interface NewSessionError {
  message: string;
  errorType?: string;
  messageId?: string;
  stack?: string;
  metadata?: Map<string, unknown>;
}

/** The part of a session a run changes. Only the fields present are written. */
export interface RunStateChange {
  status?: string;
  /** A string sets it; `null` clears it. */
  failReason?: string | null;
  lastActivityAt?: number;
  /** The edited first question moves the title with it. */
  title?: string;
  /** Only fields that are neither undefined nor null are written. */
  modelInfo?: Partial<IAIModel>;
  appendError?: NewSessionError;
}

/** Typed list inputs. The query-string parsing and validation stay in the caller. */
export interface SessionListQuery {
  orgId: string;
  userId: string;
  scope: SessionScope;
  /** One conversation instead of a list. */
  id?: string;
  /** Plain chats: own rows, rows shared with the user, or both. */
  owned?: boolean;
  shared?: boolean;
  /** Agent chats: also match rows shared with the user at least at this level. */
  sharedAccess?: AccessLevel;
  /** Rows of projects the user can view, shared to the project. */
  accessibleProjectIds?: string[];
  /** Title or content search: the escaped regex source and the content-matching ids. */
  search?: string;
  contentMatchIds?: string[];
  startDate?: Date;
  endDate?: Date;
  /** `?shared=` filter on `isShared`. */
  isSharedFilter?: boolean;
  /** `?projectId=<id>|unassigned`. */
  projectFilter?: { kind: 'unassigned' } | { kind: 'project'; id: string };
  /** `any`: no archive condition; `no`: not archived; `notTrue`: archived flag not true (agent lists); `only`: archived with an archivist. */
  archived: 'no' | 'notTrue' | 'only' | 'any';
  sort: Record<string, 1 | -1>;
  skip: number;
  limit: number;
  /** Shared-with-me lists drop the member list from each row. */
  omitSharedWith?: boolean;
  /** Chat scope, shared-with-me only: also reach agent chats shared with the user. Owned lists stay plain chats. */
  sharedIncludesAgentChats?: boolean;
}

export interface SessionPage {
  rows: ChatSessionDto[];
  total: number;
}

export type SessionSummary = Pick<
  ChatSessionDto,
  | '_id'
  | 'orgId'
  | 'title'
  | 'initiator'
  | 'createdAt'
  | 'isShared'
  | 'sharedWith'
  | 'status'
  | 'failReason'
  | 'modelInfo'
  | 'projectId'
  | 'projectVisibility'
>;

export interface AgentArchiveGroup {
  agentKey: string;
  conversations: ChatSessionDto[];
  totalCount: number;
}

export interface ArchivedSearchResult {
  rows: Array<ChatSessionDto & { source: 'agent' | 'assistant' }>;
  assistantCount: number;
  agentCount: number;
}

/**
 * Sessions with a message whose text matches. `truncated`: the search was cut short (more matches than `limit`), so older
 * matches may be missing.
 */
export interface VisibleSummaryRow {
  _id: string;
  title?: string;
  sessionType?: 'chat' | 'agent';
  agentKey?: string;
  projectId?: string;
  isArchived?: boolean;
}

export interface ContentMatches {
  ids: string[];
  truncated: boolean;
}

export interface AttachmentsAndCitedRecords {
  attachments?: Array<{ recordId?: string }>;
  citations?: Array<{
    citationId?:
      { metadata?: { recordId?: string; connector?: string } } | string;
  }>;
}

export interface ConversationRepository {
  /** False when the store is not connected (unit tests); callers skip optional reads then. */
  isConnected(): boolean;
  /** A session by id alone, for reloading a row the caller already authorised. */
  findById(id: string): Promise<ChatSessionDto | null>;
  // ---- sessions: create and read
  /** Creates the session and its first message together (one `Tx` when given). */
  openSession(
    fields: NewSession,
    firstMessage: NewMessage,
    tx?: Tx,
  ): Promise<ChatSessionDto>;
  /** Creates an empty session; its messages are appended after. */
  createSession(fields: NewSession, tx?: Tx): Promise<ChatSessionDto>;
  /** The owner only; the row a new turn is added to. A member a chat is shared with, write level included, can read it but not continue it. */
  findForTurn(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null>;
  /** Agent chats: owner or member (write level when asked); a malformed id finds nothing. */
  findAgentWithAccess(
    id: string,
    agentKey: string,
    actor: SessionActor,
    level: AccessLevel,
  ): Promise<ChatSessionDto | null>;
  /** Owner-only (also the initiator) lookups for title, project link and visibility changes. */
  findOwnedByInitiator(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
  ): Promise<ChatSessionDto | null>;
  /** The caller's own conversation by user id, for retitling. */
  findOwned(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null>;
  /** Initiator of a conversation the caller may share. Plain chats unless a scope says otherwise. */
  findForShare(
    id: string,
    actor: SessionActor,
    scope?: SessionScope,
  ): Promise<ChatSessionDto | null>;
  findForUnshare(
    id: string,
    actor: SessionActor,
    scope?: SessionScope,
  ): Promise<ChatSessionDto | null>;
  /** The conversation the owner may delete. */
  findDeletable(
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null>;
  /** The owner; the row to (un)archive. */
  findArchivable(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null>;
  /** Chats: initiator or a member; agent chats: the owner. The row feedback is left on. */
  findForFeedback(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null>;
  /** The projection the conversation page needs, for what the user can see. */
  findVisibleSummary(q: SessionListQuery): Promise<SessionSummary | null>;
  listSessions(q: SessionListQuery): Promise<SessionPage>;
  /** Rows shared with the user for one agent. */
  listAgentSharedWithMe(q: {
    orgId: string;
    userId: string;
    agentKey: string;
    status?: string;
    sort: Record<string, 1 | -1>;
    skip: number;
    limit: number;
  }): Promise<ChatSessionDto[]>;
  /** Archived chats and agent chats matching a title or content search, merged. */
  searchArchived(q: {
    orgId: string;
    userId: string;
    escapedSearch: string;
    contentMatchIds: string[];
    deletedAgentKeys: string[] | null;
    skip: number;
    limit: number;
  }): Promise<ArchivedSearchResult>;
  /** The user's archived agent chats grouped by agent. */
  listArchivedAgentGroups(q: {
    orgId: string;
    userId: string;
    deletedAgentKeys: string[] | null;
    agentSkip: number;
    agentLimit: number;
    chatsPerAgent: number;
  }): Promise<{ groups: AgentArchiveGroup[]; totalAgents: number }>;
  /** Ids of the sessions with a message whose text matches. */
  findIdsByContent(
    orgId: string,
    escapedSearch: string,
    limit?: number,
  ): Promise<ContentMatches>;
  /**
   * Title, agent and project of the sessions the user may see, for display only.
   * An archived chat is returned only where the archive can open it: to its
   * owner, and to people it is shared with when it is an assistant chat.
   */
  listVisibleSummaries(
    orgId: string,
    userId: string,
    ids: string[],
    accessibleProjectIds: string[],
  ): Promise<VisibleSummaryRow[]>;
  /** Project conversation list (owner's rows, or project-visible ones). */
  listByProject(q: {
    orgId: string;
    projectId: string;
    userId: string;
    skip: number;
    limit: number;
  }): Promise<SessionPage>;
  /**
   * Live chats per project that this person can open: their own, and the ones
   * shared to the project. Another member's private chat is not counted.
   */
  countByProject(
    orgId: string,
    userId: string,
    projectIds: string[],
  ): Promise<Map<string, number>>;
  /** Clears the project link of every session of a project. */
  unlinkProject(projectId: string, tx?: Tx): Promise<void>;
  /** Clears one session's project link, only while it still points at `projectId`. */
  unlinkFromProject(
    orgId: string,
    sessionId: string,
    projectId: string,
  ): Promise<void>;

  // ---- sessions: change
  /** Applies a run's outcome to the session and returns it as saved; null when the session is gone. */
  recordRunState(
    id: string,
    change: RunStateChange,
    tx?: Tx,
  ): Promise<ChatSessionDto | null>;
  setTitle(id: string, title: string, tx?: Tx): Promise<ChatSessionDto | null>;
  /** Replaces the member list. `clearShareLink` also clears the link when nobody is left. */
  setSharing(
    scope: SessionScope,
    id: string,
    patch: {
      isShared?: boolean;
      sharedWith: SessionShareDto[];
      clearShareLink?: boolean;
    },
    tx?: Tx,
  ): Promise<ChatSessionDto | null>;
  setArchived(
    scope: SessionScope,
    id: string,
    archived: { by: string } | null,
    tx?: Tx,
  ): Promise<ChatSessionDto | null>;
  /** Removes a session and its messages outright: for a copy that failed half-way, never shown to anyone. */
  discardSession(id: string, orgId: string, tx?: Tx): Promise<void>;
  /**
   * The owner pins or unpins their own chat. Pinning an archived chat finds nothing; unpinning always works so a stale pin can be
   * cleared. Neither changes `updatedAt`. Repeating either is a no-op that still returns the row.
   */
  setPinned(
    id: string,
    actor: SessionActor,
    pinned: boolean,
    tx?: Tx,
  ): Promise<ChatSessionDto | null>;
  /** Soft delete of a chat with the soft delete of the citations its messages carry. */
  softDelete(
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<{ session: ChatSessionDto; citationIds: string[] } | null>;
  /** Soft delete of an agent chat by a member with write access. */
  softDeleteAgent(id: string, userId: string): Promise<ChatSessionDto | null>;
  setProjectLink(
    scope: SessionScope,
    id: string,
    link: {
      projectId: string;
      projectVisibility: 'private' | 'project';
    } | null,
  ): Promise<ChatSessionDto | null>;
  setProjectVisibility(
    scope: SessionScope,
    id: string,
    visibility: 'private' | 'project',
  ): Promise<ChatSessionDto | null>;

  // ---- run lease (never inside a turn transaction)
  reserveRun(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    settleMs?: number,
  ): Promise<RunReservation | null>;
  releaseRun(r: RunReservation): Promise<void>;
  touchRun(id: string, runId?: string): Promise<void>;

  // ---- messages
  appendMessages(
    sessionId: string,
    orgId: string,
    msgs: NewMessage[],
    tx?: Tx,
  ): Promise<MessageDto[]>;
  /** Replaces a message wholesale, keeping its id, position and author. */
  replaceMessage(
    messageId: string,
    orgId: string,
    msg: NewMessage,
    tx?: Tx,
  ): Promise<MessageDto | null>;
  /** `sessionId` narrows the delete to one session's rows, so an id from elsewhere matches nothing. */
  deleteMessages(
    messageIds: string[],
    orgId: string,
    tx?: Tx,
    sessionId?: string,
  ): Promise<number>;
  /**
   * Rewrites a stored `user_query` row of the session (an edited question): only `content`, and `attachments` when given
   * (omitted keeps the stored files). Mentions and seq stay. Null when the id is not a question of that session.
   */
  updateUserQueryContent(
    messageId: string,
    orgId: string,
    sessionId: string,
    content: string,
    attachments?: IChatAttachmentRef[],
    tx?: Tx,
  ): Promise<MessageDto | null>;
  /** The message after the change. A store that projects per row returns it without the fields only its author may see. */
  appendFeedback(
    messageId: string,
    orgId: string,
    feedback: unknown,
    tx?: Tx,
  ): Promise<MessageDto | null>;
  /** One message of a session. */
  findMessage(
    sessionId: string,
    orgId: string,
    messageId: string,
    tx?: Tx,
  ): Promise<MessageDto | null>;
  /** Oldest first unless `order` is 'desc'. Never returns messages for more than one session. */
  listMessages(
    sessionId: string,
    q: {
      orgId: string;
      skip?: number;
      limit?: number;
      withCitations?: boolean;
      order?: 'asc' | 'desc';
    },
    tx?: Tx,
  ): Promise<MessageRow[]>;
  /** One page of sessions' messages in a single read, grouped by session, oldest first. */
  listMessagesOfSessions(
    sessionIds: string[],
    q: { orgId: string; withCitations?: boolean },
  ): Promise<Map<string, MessageRow[]>>;
  /** The text of the messages up to `lastSeq` that carry an artifact marker (`::artifact[`), for a fork's artifact copy. */
  listArtifactMarkerContents(
    sessionId: string,
    orgId: string,
    lastSeq: number,
  ): Promise<string[]>;
  countMessages(sessionId: string, orgId: string): Promise<number>;
  /** Attachment ids and the cited record of each citation, for permission sync. */
  listAttachmentsAndCitedRecords(
    sessionId: string,
  ): Promise<AttachmentsAndCitedRecords[]>;

  // ---- citations
  createCitations(cs: NewCitation[], tx?: Tx): Promise<CitationDto[]>;
  getCitations(ids: string[], orgId: string): Promise<CitationDto[]>;
}
