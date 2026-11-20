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
  /** Compare models: present only on a hidden branch, the id of the chat it branched from. */
  compareOf?: string;
  compareFromMessageId?: string;
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
  compareOf?: string;
  compareFromMessageId?: string;
  /** The agent harness's executor pin and import ledger, for a session it creates (a durable chat, a fork). */
  harnessVersion?: HarnessVersionDto;
  harnessLedger?: HarnessLedgerDto;
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
  /** The version of the stored content (an answer or error row); its label is never returned. */
  labelVer?: string;
  createdAt?: Date;
  updatedAt?: Date;
  /** The agent harness's fields (WP109-FU-2); `label` only when read `withLabels`. */
  senderId?: string;
  foreignTurn?: boolean;
  copied?: boolean;
  runId?: string;
  errorCode?: string;
  errorServers?: string[];
  errorStop?: IMessage['errorStop'];
  truncatedBy?: IMessage['truncatedBy'];
  budgetScope?: IMessage['budgetScope'];
  runOutcome?: IMessage['runOutcome'];
  notFound?: IMessage['notFound'];
  limits?: IMessage['limits'];
  command?: IMessage['command'];
  label?: IMessage['label'];
}

/** The first question or note of a session, removed or not: what a title made from the session's first message came from. */
export interface OpeningMessageDto {
  content: string;
  appliedFilters?: IAppliedFilters;
}

/**
 * A message with what the reader check reads of it, its label and archive included. Read by the reader
 * check only; nothing built from it is returned to a client.
 */
export interface MessageLabelRow {
  _id: string;
  sessionId: string;
  orgId: string;
  /** Its place in its chat: where its turn is read from when an answer from before labels is checked. */
  seq?: number;
  messageType: string;
  content: string;
  parts?: unknown[];
  labelVer?: string;
  provenance?: unknown;
  labelHistory?: unknown[];
  requestedBy?: string;
  authorUserId?: string;
  runId?: string;
  respondingAgentKey?: string;
  sharedFrom?: unknown;
  consent?: unknown;
  consentTo?: unknown;
  createdAt?: Date;
  removedAt?: Date;
}

/**
 * The reader check's closure of one answer version, as stored (`StoredClosure` in `privacy/closure.ts`): keyed
 * `rowId@ver`, of one org, never returned to a client. Every field is named: a store that left one out would keep
 * closures no read can take.
 */
export interface AnswerClosureDoc {
  _id: string;
  orgId: string;
  sessionId?: string;
  rowId: string;
  ver: string;
  rev: number;
  self: unknown;
  add: unknown;
  chain: unknown;
  folded: unknown;
  size: number;
  spans: unknown;
  pub: unknown;
  shares: unknown;
  digest: string;
  at: Date;
}

/** A message that carries a share, on its current version (`consent`) or an archived one (`labelHistory[].consent`). */
export interface ShareMarkRow {
  _id: string;
  sessionId: string;
  labelVer?: string;
  consent?: unknown;
  labelHistory?: Array<{ ver?: unknown; consent?: unknown }>;
}

/** A message as the store returns it. */
export interface MessageRow {
  dto: MessageDto;
}

/** An answer (or failed answer) row of one chat, as the answer-share writes address it. */
export interface AnswerRowRef {
  orgId: string;
  sessionId: string;
  messageId: string;
}

/** A member's ask to see one version of an answer. */
export interface ShareRequestDto {
  by: string;
  at: Date;
  /** The answer's `labelVer` asked about. */
  ver: string;
  state: 'open' | 'shared' | 'dismissed' | 'void';
  /** The asking member's membership when they asked: a removal and re-add voids the ask. */
  memberSince?: string;
}

/** How an ask is closed in place: answered by a share is `setAnswerConsent`'s. */
export type ClosedShareRequestState = 'dismissed';

/** The asker's consent to show one version of an answer to more people than can open what it used. */
export interface AnswerConsentDto {
  by: string;
  at: Date;
  ver: string;
  scope: 'everyone' | 'people';
  how?: 'earlier';
}

/** One person a share with people covers, in the membership they had when it was made. */
export interface AnswerConsentEntryDto {
  userId: string;
  memberSince: string;
}

export interface NewAnswerShare {
  /** The answer's version as read; absent for a row from before labels. */
  labelVer: string | undefined;
  /** The share as read (null: none): written only over that one. */
  expected: AnswerConsentDto | null;
  consent: AnswerConsentDto;
  /** A share with people names them; a share with everyone stores no one. */
  consentTo?: AnswerConsentEntryDto[];
  /** Whose open asks for `consent.ver` this share answers. */
  covers: 'everyone' | readonly string[];
}

/** A share that was ended, with whom it covered. */
export interface EndedAnswerShare {
  consent: AnswerConsentDto;
  consentTo: AnswerConsentEntryDto[];
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
  | 'metadata'
  | 'agentKey'
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

export type HarnessVersionDto = 'legacy' | 'durable-v1';

/** The durable harness's import ledger: the messages it accounted for, up to `seq` (agent-harness session-import). */
export interface HarnessLedgerDto {
  seq: number;
  digest: string;
  diverged?: boolean;
}

/** A session with the agent harness's own fields, which no other read returns (WP109-FU-2). Deleted sessions included. */
export interface HarnessSessionRow {
  /** As a lean read gives it, `sharedWith` included. */
  session: ChatSessionDto;
  sessionType: 'chat' | 'agent';
  nextSeq: number;
  harnessVersion?: HarnessVersionDto;
  harnessLedger?: HarnessLedgerDto;
  erasureRequestedAt?: number;
}

/** The agent harness's fields a write sets, with the session's status and activity when given. Only the fields present are written. */
export interface HarnessSessionPatch {
  harnessVersion?: HarnessVersionDto;
  harnessLedger?: HarnessLedgerDto;
  erasureRequestedAt?: number;
  status?: string;
  lastActivityAt?: number;
}

/** A deleted session whose erasure was asked for and has not finished (the marker goes with the session). */
export interface ErasureRequestDto {
  orgId: string;
  sessionId: string;
  requestedAt: number;
  deletedBy?: string;
}

/** A session's deletion fields and how many messages and citation links it holds (the erasure backfill). */
export interface SessionDeletionDto {
  orgId: string;
  sessionId: string;
  isDeleted: boolean;
  deletedBy?: string;
  erasureRequestedAt?: number;
  updatedAt?: Date;
  messages: number;
  citations: number;
}

/** A person's own conversation, as `listUserSessionsAfter` lists it. */
export interface UserSessionDto {
  _id: string;
  sessionType: 'chat' | 'agent';
  title?: string;
  agentKey?: string;
  createdAt?: Date;
  updatedAt?: Date;
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
  /** The caller's own live, unarchived chats copied from `sourceId`, oldest first, so the original can link to its forks. */
  listForksOf(
    scope: SessionScope,
    sourceId: string,
    actor: SessionActor,
  ): Promise<ChatSessionDto[]>;
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
  /**
   * Ids of `userId`'s own sessions with a message of theirs whose text matches:
   * their questions and answers, never tool output. A chat shared with them or
   * visible through a project matches by title alone (`listSessions` with
   * `search`): a message there can be an answer hidden from them, and a match on
   * its text would tell whether it holds the words searched for.
   */
  findIdsByContent(
    orgId: string,
    userId: string,
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
  /** Compare models: the hidden branches of a chat that its owner started, oldest first. */
  listBranches(
    orgId: string,
    parentId: string,
    userId: string,
    tx?: Tx,
  ): Promise<ChatSessionDto[]>;
  /** Compare models: every branch the user started, in any chat, last touched before `beforeMs`. */
  listStaleBranches(
    orgId: string,
    userId: string,
    beforeMs: number,
  ): Promise<ChatSessionDto[]>;
  /** Compare models: removes branches of a chat for good, with their messages. Every branch when `ids` is omitted. */
  discardBranches(
    orgId: string,
    parentId: string,
    ids?: string[],
    tx?: Tx,
  ): Promise<void>;
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
  /**
   * Sets the title only while it is still `expected`, so a rename made meanwhile is never overwritten, and moves the
   * session's change marker where the store keeps one. False when the title changed or the session is gone.
   */
  replaceTitle(
    sessionId: string,
    orgId: string,
    expected: string,
    title: string,
    tx?: Tx,
  ): Promise<boolean>;
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
  /** Removes a session and its messages outright: for a copy that failed half-way, never shown to anyone, and for erasure. */
  discardSession(
    id: string,
    orgId: string,
    tx?: Tx,
  ): Promise<{ sessions: number; messages: number }>;
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
  softDeleteAgent(
    id: string,
    userId: string,
    tx?: Tx,
  ): Promise<ChatSessionDto | null>;
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
  /**
   * Messages of the org by id, in any of its chats and removed ones included, with their labels: for the
   * reader check only. Ids that are not messages of the org are left out.
   */
  listMessageLabels(
    orgId: string,
    messageIds: string[],
  ): Promise<Map<string, MessageLabelRow>>;
  /**
   * The answers and errors of these chats of the org that one of `runIds` wrote, by the run of their current version
   * (`runId`) or of an archived one (`labelHistory[].runId`), read as `listMessageLabels` reads them; removed ones
   * included. For the reader check only: a file a run read takes the label of the version its writing run wrote. A
   * store that keeps no runs on a message finds none, so such a file hides what was built on it.
   */
  listMessagesOfRuns(
    orgId: string,
    sessionIds: string[],
    runIds: string[],
  ): Promise<MessageLabelRow[]>;
  /**
   * The stored closures of answer versions of the org, by key (`rowId@ver`); a key with none is left out. For the reader
   * check only. A store that keeps no closures finds none, and the reader check makes them on every read.
   */
  listAnswerClosures(
    orgId: string,
    keys: string[],
  ): Promise<Map<string, AnswerClosureDoc>>;
  /** Stores closures of answer versions of the org, each replacing any stored under its key. */
  saveAnswerClosures(
    orgId: string,
    closures: AnswerClosureDoc[],
  ): Promise<void>;
  /**
   * The messages of these chats of the org that carry a share, on their current version or an archived one, with
   * nothing else of them: what the reader check reads to notice a share made after a closure. A store that keeps no
   * shares on a message (it reads them as absent everywhere) finds none.
   */
  listShareMarks(orgId: string, sessionIds: string[]): Promise<ShareMarkRow[]>;
  /** The session's first question or note, removed or not; null when it has none. */
  findOpeningMessage(
    sessionId: string,
    orgId: string,
    tx?: Tx,
  ): Promise<OpeningMessageDto | null>;
  /** One message of a session. */
  findMessage(
    sessionId: string,
    orgId: string,
    messageId: string,
    tx?: Tx,
  ): Promise<MessageDto | null>;
  /**
   * Oldest first unless `order` is 'desc'. Never returns messages for more than one session. With `maxSeq`, only the
   * messages up to that place in the chat (the turn a message closes, read newest first).
   */
  listMessages(
    sessionId: string,
    q: {
      orgId: string;
      skip?: number;
      limit?: number;
      withCitations?: boolean;
      order?: 'asc' | 'desc';
      /** A durable answer's label (P2.5a), never read unless asked for: it names sources a reader may not see. */
      withLabels?: boolean;
      /** Only messages after this seq (a keyset page); with `skip` it counts from there. */
      afterSeq?: number;
      maxSeq?: number;
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

  // ---- answer shares: one atomic write each, guarded by what must still hold, so concurrent asks never refuse each
  // other or the asker's own share, dismiss or stop; each gives the row a fresh change stamp
  /**
   * Adds a member's ask while the answer is still at `labelVer`: one ask per person per version, and one more after each
   * share that answered theirs stops covering them. The write holds the rule: no ask of theirs for the version may be
   * open, dismissed or void, and if they asked before, the answer's share must still be `share`, the one the caller read
   * and found no longer covers them (the reader check's `consentCovers` decides that, so this store does not). False
   * when the answer moved on, the row is gone or the rule no longer holds: asks arriving together, a double click
   * included, add one.
   */
  addShareRequest(
    row: AnswerRowRef,
    labelVer: string | undefined,
    request: ShareRequestDto,
    share: AnswerConsentDto | null,
    tx?: Tx,
  ): Promise<boolean>;
  /**
   * Closes the open asks for version `ver` (only those of `by`, when given) where they are. Whose asks it closed; none
   * when no such ask was open, and then nothing is written.
   */
  closeShareRequests(
    row: AnswerRowRef,
    asks: {
      ver: string;
      state: ClosedShareRequestState;
      by?: readonly string[];
    },
    tx?: Tx,
  ): Promise<string[]>;
  /**
   * Shares the answer while it is still at `labelVer` and its share is still `expected`, and answers the open asks it
   * covers where they are. Whose asks it answered; null when the answer or its share changed since they were read.
   */
  setAnswerConsent(
    row: AnswerRowRef,
    share: NewAnswerShare,
    tx?: Tx,
  ): Promise<string[] | null>;
  /**
   * Ends the answer's share, whatever version it was for (only one `onlyBy` made, when given): the share it ended,
   * with whom it covered; null when there was none.
   */
  clearAnswerConsent(
    row: AnswerRowRef,
    onlyBy?: string,
    tx?: Tx,
  ): Promise<EndedAnswerShare | null>;

  // ---- citations
  createCitations(cs: NewCitation[], tx?: Tx): Promise<CitationDto[]>;
  getCitations(ids: string[], orgId: string): Promise<CitationDto[]>;
  /** Removes citations of the org outright (erasure); the count removed. */
  deleteCitations(ids: string[], orgId: string, tx?: Tx): Promise<number>;

  // ---- the agent harness's fields and sweeps (WP109-FU-2)
  /** `forUpdate`: the session row stays locked to the end of `tx` (PostgreSQL), so a check of it holds for the write after. */
  findHarnessSession(
    id: string,
    orgId: string,
    opts?: { forUpdate?: boolean },
    tx?: Tx,
  ): Promise<HarnessSessionRow | null>;
  /** False when the session does not exist. */
  setHarnessFields(
    id: string,
    orgId: string,
    patch: HarnessSessionPatch,
    tx?: Tx,
  ): Promise<boolean>;
  /**
   * ADR-UI-01 D2: moves `userId`'s "seen up to" mark on the session to `at` unless it already is later, without
   * touching the session's own times; the mark held, or null when there is no such session.
   */
  markHarnessSeen(
    id: string,
    orgId: string,
    userId: string,
    at: number,
    tx?: Tx,
  ): Promise<number | null>;
  /** ADR-UI-01 D2: `userId`'s marks on these sessions of the org; a session they never marked is left out. */
  harnessSeenMarks(
    orgId: string,
    userId: string,
    ids: readonly string[],
  ): Promise<Map<string, number>>;
  /** Live sessions of the org last active before `before`, least recently active first. */
  listIdleSessions(
    orgId: string,
    before: number,
    limit: number,
  ): Promise<string[]>;
  /** Deletes a live session still idle since before `before`, marked for erasure at `at`; false when it no longer is. */
  expireIdleSession(
    orgId: string,
    id: string,
    before: number,
    at: number,
    tx?: Tx,
  ): Promise<boolean>;
  /** Deleted sessions of every org marked for erasure, by (requestedAt, id) after `after`. */
  listErasureRequests(
    after: { requestedAt: number; sessionId: string } | null,
    limit: number,
  ): Promise<ErasureRequestDto[]>;
  /** Deleted sessions by id after `after`, of one org or (null) of every org. */
  listDeletedSessions(
    after: string | null,
    limit: number,
    orgId: string | null,
  ): Promise<SessionDeletionDto[]>;
  /** The named sessions of the org, deleted or not, by id. */
  sessionDeletionStates(
    orgId: string,
    ids: readonly string[],
  ): Promise<SessionDeletionDto[]>;
  /** The user's own sessions that are not deleted (archived included), chats and agent chats, by id after `after`. */
  listUserSessionsAfter(
    orgId: string,
    userId: string,
    after: string | null,
    limit: number,
  ): Promise<UserSessionDto[]>;
  /** Marks the attachments naming `recordIds` removed in the org's messages outside one session; the messages changed. */
  markAttachmentsRemoved(
    orgId: string,
    exceptSessionId: string,
    recordIds: readonly string[],
  ): Promise<number>;
  /** For each record id some message of the org attaches, one session whose message does. */
  findAttachmentCarriers(
    orgId: string,
    recordIds: readonly string[],
  ): Promise<Map<string, string>>;
  /** The titles of the org's chats among `ids` (plain and agent chats alike) that are not deleted, by id; for display only. */
  listTitles(
    orgId: string,
    ids: readonly string[],
  ): Promise<Map<string, string>>;

  // ---- an org's or a person's deletion (ORGDEL, CLEANUP-G1, G2)
  /** The person's own sessions that are not deleted, by id; `shared` when someone else holds a share of it. */
  listOwnedForHandover(
    orgId: string,
    userId: string,
  ): Promise<Array<{ sessionId: string; shared: boolean }>>;
  /** The sessions `fromUserId` owns among `ids` become `toUserId`'s (owner and initiator) and `toUserId` no longer holds a share of them; how many moved. */
  transferOwners(
    orgId: string,
    ids: readonly string[],
    fromUserId: string,
    toUserId: string,
    tx?: Tx,
  ): Promise<number>;
  /** Marks the sessions deleted with their erasure asked for at `at` (an earlier request's time stands); how many there were. */
  markDeletedForErasure(
    orgId: string,
    ids: readonly string[],
    at: number,
    tx?: Tx,
  ): Promise<number>;
  /** Every session id of the org, deleted or not, after `after` in id order. */
  listSessionIdsAfter(
    orgId: string,
    after: string | null,
    limit: number,
  ): Promise<string[]>;
  /** Removes up to `limit` of the org's citations (its erasure); how many it removed. */
  deleteOrgCitations(orgId: string, limit: number): Promise<number>;
}
