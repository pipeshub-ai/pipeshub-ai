import { parseTurnLabel } from '../../agent-harness/domain/turn-labels';
import {
  couldNotDoOf,
  budgetScopeField,
  notFoundOf,
} from '../../agent-harness/projection/timeline';
import { withSavedEngineLines } from '../../agent-harness/projection/legacy-engine-lines';
import type { OutcomeView } from '../../agent-harness/projection/outcome-view';
import {
  SessionRole,
  sessionRoleOf,
} from '../../agent-harness/domain/session-roles';
import {
  historySender,
  ownerHistoryTurns,
} from '../../agent-harness/domain/session-audience';
import type { AppConfig } from '../../tokens_manager/config/config';
import {
  messageActor,
  redactConversationFor,
  redactMessagesFor,
  viewerOf,
  type ViewerMessage,
} from './viewer-redaction';
import { asQueryFilter } from '../../../libs/utils/mongo-filter';
import {
  idOf as idOfValue,
  isWithheld,
  visibleRows,
  WITHHELD,
  rememberAnswerClosures,
  type ReaderSession,
} from '../services/conversation/privacy/visibility';
import {
  AIServiceResponse,
  IAIModel,
  IAppliedFilters,
  IChatAttachmentRef,
  IChatSession,
  IChatSessionDocument,
  IChatSessionMessage,
  IChatSessionMessageDocument,
  IFeedback,
  IMessage,
  IMessageCitation,
  IMessageDocument,
  IMessagePart,
} from '../types/conversation.interfaces';
import {
  storedQuestion,
  type RunCommand,
} from '../../agent-harness/domain/commands';
import {
  CUT_SHORT_REASONS,
  type CutShortReason,
} from '../../agent-harness/contracts/generated/core/enums';
import {
  type NewSessionError,
  type RunStateChange,
  type SessionListQuery,
} from '../../../libs/repositories/conversation';
import { isValidId, newObjectId } from '../../../libs/repositories/ids';
import { IAIResponse } from '../types/conversation.interfaces';
import { randomUUID } from 'crypto';
import mongoose, { ClientSession, QueryFilter } from 'mongoose';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  ConflictError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import Citation, { ICitation } from '../schema/citation.schema';
import {
  CONVERSATION_STATUS,
  ONLY_AGENT,
  REASONING_EFFORT_VALUES,
} from '../constants/constants';
import { Logger } from '../../../libs/services/logger.service';
import { Response } from 'express';
import { ChatSession } from '../schema/chat.session.schema';
import {
  ChatSessionMessage,
  NOT_REMOVED,
} from '../schema/chat.session.message.schema';
import { AccessView } from '../services/conversation/domain/types';
import {
  conversationExtensions,
  PreviousToolResult,
  viewMessages,
} from '../services/conversation/conversation-extensions';
import {
  LeaseHandle,
  LeaseLostError,
} from '../services/conversation/turn/run-lease';
import {
  stampTurnRow,
  TurnRun,
  turnWrite,
} from '../services/conversation/turn/turn-run';
import type { TurnAnswerHooks } from '../services/conversation/turn/turn-extension';
import { MongoUserDirectory } from '../../user_management/services/user-directory.service';
import { safeParsePagination } from '../../../utils/safe-integer';
import {
  sanitizeForResponse,
  validateBooleanParam,
  validateNoXSS,
  validateNoFormatSpecifiers,
} from '../../../utils/xss-sanitization';
import {
  AGUIEventType,
  aguiRunErrorMetadata,
  frameAGUI,
  isAGUI,
  readRunFrame,
  SSEProtocol,
} from './agui';
import type { StreamedTranscript } from './turn-stream';
import { logBadFrame } from './chat-stream-run';
import {
  CHAT_ERROR_MESSAGES,
  userFacingChatError,
} from './chat-error-messages';
import { stripModelAuthoredMarkers } from './answer-markers';
import { stripSignedUrlLinks } from './signed-url';
import { sanitizeAnswerMessage } from './answer-sanitizer';
import { sanitizeUnavailableTools } from './unavailable-tools';
import { adminRefusals, AIRequestRefusedError } from './ai-stream';
import {
  answerLabelFrom,
  archivedLabelHistory,
  replacedLabelHistory,
  sealAnswerForStorage,
  withoutLabelFields,
} from '../services/conversation/privacy/label';
import { StreamRelay } from './relay-events';
import { createUnitOfWork } from '../../../libs/repositories/doc-store';
import { sessionOf } from '../../../libs/repositories/mongo';
import type { Tx } from '../../../libs/repositories/uow';
import { isReplicaSet } from '../../../libs/utils/replica-set';
import { markErasureRequested } from '../../../libs/repositories/agent-harness/session-fields';

const logger = new Logger({
  service: 'enterprise-search',
});

/**
 * Type-safe `target[field] = value` for a `keyof IAIModel` loop variable.
 * Assigning directly through a widened `keyof IAIModel` union key loses the
 * per-field type correlation (e.g. `reasoningEffort`'s literal union vs
 * other fields' `string`), which TS rejects; tying `field` and `value` to
 * the same generic `K` here restores that correlation.
 */
export function assignAiModelField<K extends keyof IAIModel>(
  target: IAIModel,
  field: K,
  value: IAIModel[K],
): void {
  target[field] = value;
}

/**
 * Extract model information from request body
 */
export const extractModelInfo = (
  body: any,
  defaultChatMode: string = 'quick',
): IAIModel => {
  // Use modelFriendlyName if provided and not empty, otherwise fallback to modelName for backward compatibility
  const modelFriendlyName = body.modelFriendlyName?.trim()
    ? body.modelFriendlyName.trim()
    : body.modelName || undefined;

  return {
    modelKey: body.modelKey || undefined,
    modelName: body.modelName || undefined,
    modelProvider: body.modelProvider || undefined,
    chatMode: body.chatMode || defaultChatMode,
    modelFriendlyName: modelFriendlyName,
    reasoningEffort: body.reasoningEffort || undefined,
  };
};
export { conversationTitleFromQuery } from './quoted-selection';

/** Who wrote a `user_query` row, and any other fields it is stored with. */
export type UserQueryAuthorship = Partial<IMessage>;

export const buildUserQueryMessage = (
  query: string,
  appliedFilters?: IAppliedFilters,
  chatMode?: string,
  attachments?: IChatAttachmentRef[],
  command?: RunCommand,
  authorship?: UserQueryAuthorship,
  // A question carries no citations, so the row suits both the conversation's and the repository's message types.
): Omit<IMessage, 'citations'> => ({
  messageType: 'user_query',
  content: storedQuestion(query, command),
  contentFormat: 'MARKDOWN',
  ...(appliedFilters ? { appliedFilters } : {}),
  modelInfo: chatMode ? ({ chatMode } as IAIModel) : undefined,
  ...(attachments && attachments.length > 0 ? { attachments } : {}),
  ...(command !== undefined ? { command } : {}),
  ...definedFields(authorship),
  createdAt: new Date(),
  updatedAt: new Date(),
});

const definedFields = <T extends object>(fields: T | undefined): Partial<T> =>
  Object.fromEntries(
    Object.entries(fields ?? {}).filter(([, value]) => value !== undefined),
  ) as Partial<T>;

/**
 * Safely extracts and validates a search parameter from query string
 * Prevents type confusion by ensuring the parameter is a string, not an array
 * @param searchParam - The search parameter from req.query.search
 * @returns A validated string value
 * @throws BadRequestError if the parameter is an array or not a string
 */
function extractSearchParameter(searchParam: unknown): string {
  // First check: reject arrays explicitly
  if (Array.isArray(searchParam)) {
    throw new BadRequestError(
      'Search parameter must be a string, not an array',
    );
  }
  // Second check: ensure it's a string type
  if (typeof searchParam !== 'string') {
    throw new BadRequestError('Search parameter must be a string');
  }
  // Return the validated string
  return searchParam;
}

/**
 * Shared XSS-validation + regex-escaping for the title/content search param,
 * used by `buildFilter` and by the callers' async content-match lookup (see
 * `findSessionIdsMatchingContent`) so the two computations of "the escaped
 * search term" can never drift.
 */
export const validateAndEscapeSearch = (
  searchParam: unknown,
  options: { formatSpecifiers?: boolean } = {},
): string => {
  const searchValue = extractSearchParameter(searchParam);

  validateNoXSS(searchValue, 'search parameter');
  if (options.formatSpecifiers) {
    validateNoFormatSpecifiers(searchValue, 'search parameter');
  }

  if (searchValue.length > 1000) {
    throw new BadRequestError(
      'Search parameter too long (max 1000 characters)',
    );
  }

  // Escape special regex characters to prevent regex injection
  return searchValue.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
};

/**
 * The caller's own chats (`userId` is `ownerId`) holding a message of theirs whose
 * text matches (case-insensitive substring), in one org: their own questions and
 * answers, never tool output. A chat shared with the caller or visible through a
 * project, and another member's turn in the caller's chat, match by title alone
 * (`listSearchClause`): any message there can be an answer hidden from the
 * caller, and a match on its text would tell whether it holds the words searched.
 * An unanchored `$regex` can't use an index, so this scans the messages of the
 * newest `limit` of those chats that `sessionFilter` also accepts, so matches
 * elsewhere can never crowd them out of the capped result.
 */
/** The rows an answer is stored as, which a reader may be shown or not. */
const ANSWER_ROW_TYPES: Array<'bot_response' | 'error'> = [
  'bot_response',
  'error',
];
/** A search checks this many of the caller's newest matching answers. */
const SEARCH_ANSWERS_CHECKED = 200;
/** The search's reader check gives up after this; what it could not check does not match. */
const SEARCH_CHECK_TIMEOUT_MS = 1000;

export const findSessionIdsMatchingContent = async (
  orgId: string,
  escapedSearch: string,
  {
    ownerId,
    sessionFilter,
    limit = 10000,
  }: {
    ownerId: string;
    sessionFilter?: QueryFilter<IChatSessionDocument>;
    limit?: number;
  },
): Promise<mongoose.Types.ObjectId[]> => {
  if (!mongoose.isValidObjectId(ownerId)) {
    return [];
  }
  const owner = new mongoose.Types.ObjectId(ownerId);
  const ownChats: QueryFilter<IChatSessionDocument> = {
    orgId: new mongoose.Types.ObjectId(orgId),
    userId: owner,
  };
  const own = (
    await ChatSession.find(
      sessionFilter ? { $and: [sessionFilter, ownChats] } : ownChats,
    )
      .sort({ lastActivityAt: -1, _id: -1 })
      .limit(limit)
      .select('_id')
      .lean()
      .exec()
  ).map((row) => row._id);
  if (own.length === 0) {
    return [];
  }
  const matching = {
    orgId: new mongoose.Types.ObjectId(orgId),
    sessionId: { $in: own },
    // A row with no author is the owner's: only the owner added turns before authorship was stored.
    authorUserId: { $in: [owner, null] },
    requestedBy: { $in: [owner, null] },
    messageType: { $ne: 'tool_call' },
    content: { $regex: escapedSearch, $options: 'i' },
    ...NOT_REMOVED,
  };
  // The caller's own words match as they are; their own answers only while they may still see them (below).
  const rows = await ChatSessionMessage.aggregate<{
    _id: mongoose.Types.ObjectId;
  }>([
    {
      $match: {
        ...matching,
        messageType: { $nin: ['tool_call', ...ANSWER_ROW_TYPES] },
      },
    },
    { $group: { _id: '$sessionId' } },
    // One more than the cap, to tell whether the search was cut short.
    { $limit: limit + 1 },
  ]);
  if (rows.length > limit) {
    logger.warn(
      'Chat content search was cut short; older matches may be missing',
      { orgId, matched: limit },
    );
  }
  const sessions = new Set(rows.slice(0, limit).map((r) => String(r._id)));
  // An answer can be hidden from its own asker (a source they lost, a label the check can't rely on): it matches
  // only when the reader check shows it to them. The newest matches are checked; past them, fewer hits, never more.
  const answers = await ChatSessionMessage.find(
    asQueryFilter<IChatSessionMessageDocument>({
      ...matching,
      messageType: { $in: ANSWER_ROW_TYPES },
    }),
  )
    .sort({ _id: -1 })
    .limit(SEARCH_ANSWERS_CHECKED)
    .select('_id sessionId messageType requestedBy labelVer content parts')
    .lean<
      Array<{
        _id: mongoose.Types.ObjectId;
        sessionId: mongoose.Types.ObjectId;
      }>
    >()
    .exec();
  const unseen = answers.filter((a) => !sessions.has(String(a.sessionId)));
  if (unseen.length > 0) {
    const verdicts = await visibleRows(
      { userId: ownerId, orgId, member: true },
      { userId: ownerId, orgId },
      unseen,
      { member: true, ownerId, timeoutMs: SEARCH_CHECK_TIMEOUT_MS },
    );
    for (const answer of unseen) {
      const verdict = verdicts.get(String(answer._id))?.verdict;
      if (verdict === 'visible' || verdict === 'consented') {
        sessions.add(String(answer.sessionId));
      }
    }
  }
  return [...sessions]
    .slice(0, limit)
    .map((id) => new mongoose.Types.ObjectId(id));
};

/** A durable run's failure as its error message stores it (LC2FIX): its run, and its code for the web app to word. */
export interface RunFailureFields {
  runId: string;
  errorCode?: string;
  errorServers?: string[];
  /** ADR-OPS-01 D5: an `operator_stopped` run's control scope and reason. */
  errorStop?: { scope: string; reason: string };
  /** WP-89: the failure as the typed outcome an answered run carries. */
  runOutcome?: OutcomeView;
}

/** What a classic turn's failure adds to its error message: its code, and the label the AI backend sent with it. */
export interface TurnFailureFields {
  errorCode?: string;
  provenance?: unknown;
}

export const buildAIFailureResponseMessage = (
  content?: string,
  parts?: IMessagePart[],
  run?: RunFailureFields,
  turn: TurnFailureFields = {},
): IMessage => {
  // The label the AI backend sent with its error: what the failed run read decides who else sees the failure.
  // Without one, the failure is its asker's alone.
  const label = answerLabelFrom(turn.provenance);
  const errorCode = run?.errorCode ?? turn.errorCode;
  return {
    messageType: 'error',
    content: content ?? 'Error Generating Response, Please try again',
    contentFormat: 'MARKDOWN',
    ...(parts !== undefined && parts.length > 0 ? { parts } : {}),
    ...(run !== undefined ? { runId: run.runId } : {}),
    ...(errorCode !== undefined ? { errorCode } : {}),
    ...(run?.errorServers !== undefined && run.errorServers.length > 0
      ? { errorServers: run.errorServers }
      : {}),
    ...(run?.errorStop !== undefined ? { errorStop: run.errorStop } : {}),
    ...(run?.runOutcome !== undefined ? { runOutcome: run.runOutcome } : {}),
    ...(label && { provenance: label }),
    createdAt: new Date(),
    updatedAt: new Date(),
  };
};

// ---------------------------------------------------------------------------
// Core chatSessions / chatSessionMessages helpers (Phase 1)
// ---------------------------------------------------------------------------

/**
 * Allocate a contiguous block of `n` sequence numbers for a session via an
 * atomic `$inc` on the session's `nextSeq` counter. Race-free by
 * construction (concurrent callers each get a disjoint block from the same
 * counter); the unique `{sessionId, seq}` index on chatSessionMessages is
 * the backstop. Returns the END of the allocated block — the block itself
 * is `[end - n + 1 .. end]`. `seq` is a sort key only: never derive a count,
 * an index, or a page offset from it, and never assume no gaps.
 *
 * The same update takes `n` change stamps from `rev`, one per row, ending at
 * `endRev`, and moves `lastActivityAt` forward, so an append writes the
 * session once.
 */
const allocateRows = async (
  sessionId: mongoose.Types.ObjectId | string,
  n: number,
  mongoSession?: ClientSession | null,
): Promise<{ endSeq: number; endRev: number }> => {
  const updated = await ChatSession.findOneAndUpdate(
    { _id: sessionId },
    { $inc: { nextSeq: n, rev: n }, $max: { lastActivityAt: Date.now() } },
    {
      returnDocument: 'after',
      projection: { nextSeq: 1, rev: 1 }, // overrides the schema's `select: false` for this one read
      session: mongoSession || undefined,
    },
  );
  if (!updated) {
    throw new NotFoundError(
      'Chat session not found while allocating message sequence',
    );
  }
  return { endSeq: updated.nextSeq as number, endRev: updated.rev as number };
};

/** The end of the `seq` block `allocateRows` takes. */
export const allocateSeq = async (
  sessionId: mongoose.Types.ObjectId | string,
  n: number,
  mongoSession?: ClientSession | null,
): Promise<number> => (await allocateRows(sessionId, n, mongoSession)).endSeq;

/**
 * Takes `k` change stamps from the session's `rev` for rows changed in place
 * and returns the first; the block is `[first .. first + k - 1]`. Call it in
 * the same transaction as the row write, before it: then no reader can see the
 * new `rev` without the row.
 */
export const reserveChangeRevs = async (
  sessionId: mongoose.Types.ObjectId | string,
  k: number,
  mongoSession?: ClientSession | null,
): Promise<number> => {
  const updated = await ChatSession.findOneAndUpdate(
    { _id: sessionId },
    { $inc: { rev: k } },
    {
      returnDocument: 'after',
      projection: { rev: 1 },
      session: mongoSession ?? undefined,
    },
  );
  if (!updated) {
    throw new NotFoundError('Chat session not found');
  }
  return (updated.rev as number) - k + 1;
};

const SANITIZE_MAX_DEPTH = 32;
const SANITIZE_MAX_NODES = 50_000;

const isPlainObject = (value: unknown): value is Record<string, unknown> => {
  if (value === null || typeof value !== 'object') return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
};

/** Strips signed URLs from every string reachable through plain objects and
 * arrays; ObjectIds, Dates, numbers etc. pass through. A subtree past the
 * depth or node bound is dropped (null) rather than persisted unscanned. */
const stripSignedUrlsDeep = (
  value: unknown,
  depth: number,
  budget: { nodes: number },
): unknown => {
  if (typeof value === 'string') return stripSignedUrlLinks(value);
  const walkable = Array.isArray(value) || isPlainObject(value);
  if (!walkable) return value;
  if (depth >= SANITIZE_MAX_DEPTH || ++budget.nodes > SANITIZE_MAX_NODES) {
    return null;
  }
  if (Array.isArray(value)) {
    return value.map((item) => stripSignedUrlsDeep(item, depth + 1, budget));
  }
  const out: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    out[key] = stripSignedUrlsDeep(item, depth + 1, budget);
  }
  return out;
};

/**
 * The single choke point for persisted assistant data: `appendMessages` and
 * `updateMessageById` are the only writers of message content, and both pass
 * through here, so no signed URL is stored in any field (content, reasoning,
 * tool results, parts, reference data, citations, ...) whichever path
 * produced it. User queries are stored as typed.
 */
export const sanitizeMessageForPersistence = (message: IMessage): IMessage =>
  message.messageType === 'user_query' || message.messageType === 'note'
    ? message
    : (stripSignedUrlsDeep(message, 0, { nodes: 0 }) as IMessage);

/**
 * A row as it is written: no signed URL in any field, then sealed (sanitised answer text, a fresh `labelVer`, the
 * label bound to both). The label itself is kept whole: the signed-URL walk drops subtrees it cannot finish, and a
 * label is never stored cut.
 */
const storedForm = (message: IMessage): IMessage => {
  const { provenance, labelHistory, ...rest } = message;
  return sealAnswerForStorage({
    ...sanitizeMessageForPersistence(rest as IMessage),
    ...(provenance !== undefined && { provenance }),
    ...(labelHistory !== undefined && { labelHistory }),
  });
};

/** A write that archives a label is made only while the row is still at the version that was read. */
const sameLabelVersion = (row: {
  labelVer?: unknown;
}): QueryFilter<IChatSessionMessageDocument> =>
  typeof row.labelVer === 'string'
    ? { labelVer: row.labelVer }
    : { labelVer: { $exists: false } };

/** How often an archiving write reads the row again after another write moved its version; then it writes anyway. */
const LABEL_WRITE_ATTEMPTS = 3;

type StoredLabelRow = IChatSessionMessage & {
  _id: mongoose.Types.ObjectId;
  runId?: string;
};

/**
 * A document a write handed back, without its label: a copy, so the stored row keeps it. An insert returns the
 * documents it built, which hold every field it wrote, the label included.
 */
const withoutStoredLabel = (
  doc: IChatSessionMessageDocument,
): IChatSessionMessageDocument =>
  typeof (doc as Partial<IChatSessionMessageDocument>).toObject === 'function'
    ? ChatSessionMessage.hydrate(
        withoutLabelFields(doc.toObject() as Record<string, unknown>),
      )
    : withoutLabelFields(doc);

/** The row with its label fields, which no ordinary read selects. */
const readWithLabel = async (
  filter: QueryFilter<IChatSessionMessageDocument>,
  mongoSession?: ClientSession | null,
): Promise<StoredLabelRow | null> =>
  await ChatSessionMessage.findOne(
    filter,
    '+provenance +labelHistory +consentTo',
    {
      session: mongoSession || undefined,
      lean: true,
    },
  );

/**
 * Append one or more messages to a session's message collection. Allocates
 * their `seq` block first, then inserts — see the Phase 1 plan's "Ordering"
 * section for why (a crash between the two leaves nothing inconsistent: no
 * message row exists yet). Returns the inserted documents (not `.lean()`)
 * so callers can `.toObject()` them for `attachPopulatedCitations` fallback
 * without a second query.
 */
export const appendMessages = async (
  sessionId: mongoose.Types.ObjectId | string,
  orgId: mongoose.Types.ObjectId | string,
  messages: IMessage[],
  mongoSession?: ClientSession | null,
): Promise<IChatSessionMessageDocument[]> => {
  if (messages.length === 0) {
    return [];
  }
  const { endSeq, endRev } = await allocateRows(
    sessionId,
    messages.length,
    mongoSession,
  );
  const startSeq = endSeq - messages.length + 1;
  const startRev = endRev - messages.length + 1;
  const toInsert = messages.map((message, i) => ({
    ...storedForm(message),
    sessionId,
    orgId,
    seq: startSeq + i,
    changeRev: startRev + i,
  }));
  const inserted = await ChatSessionMessage.insertMany(toInsert, {
    ordered: true,
    session: mongoSession || undefined,
  });
  await rememberAnswerClosures(
    String(orgId),
    toInsert.map((row, i) => ({ ...row, _id: inserted[i]?._id })),
  );
  // An insert hands back the documents it built, label included: every caller gets them without it.
  return inserted.map(withoutStoredLabel);
};

/**
 * Wholesale-replace one message's content, preserving its `_id`/`sessionId`/
 * `orgId`/`seq`. Mirrors the old `conversation.messages[index] = newMessage`
 * array-element replacement (regeneration intentionally discards the prior
 * message's citations/feedback/etc. — only its identity and position stay
 * stable), so this is a full `findOneAndReplace`, not a `$set` merge (which
 * would leave stale fields the new content doesn't mention). The row gets a
 * fresh change stamp, taken before it is written. The label of the version it
 * replaces is archived whole into `labelHistory` in the same write.
 */
export const updateMessageById = async (
  messageId: mongoose.Types.ObjectId | string,
  orgId: mongoose.Types.ObjectId | string,
  newContent: IMessage,
  mongoSession?: ClientSession | null,
): Promise<IChatSessionMessageDocument | null> => {
  // The history comes from the stored row, never from the new content.
  const { labelHistory: _carried, ...next } = storedForm(newContent);
  for (let attempt = 1; ; attempt += 1) {
    const existing = await readWithLabel(
      { _id: messageId, orgId },
      mongoSession,
    );
    if (!existing) {
      return null;
    }
    const changeRev = await reserveChangeRevs(
      existing.sessionId,
      1,
      mongoSession,
    );
    const labelHistory = replacedLabelHistory(existing);
    const replaced = await ChatSessionMessage.findOneAndReplace(
      {
        _id: messageId,
        orgId,
        ...(attempt < LABEL_WRITE_ATTEMPTS && sameLabelVersion(existing)),
      },
      {
        ...next,
        ...(labelHistory && { labelHistory }),
        sessionId: existing.sessionId,
        orgId: existing.orgId,
        seq: existing.seq,
        changeRev,
      },
      {
        returnDocument: 'after',
        session: mongoSession || undefined,
        runValidators: true,
      },
    );
    if (replaced) {
      await rememberAnswerClosures(String(orgId), [
        {
          ...next,
          ...(labelHistory && { labelHistory }),
          _id: existing._id,
          sessionId: existing.sessionId,
          orgId: existing.orgId,
        },
      ]);
      return withoutStoredLabel(replaced);
    }
    if (attempt >= LABEL_WRITE_ATTEMPTS) return null;
  }
};

/**
 * Rewrite a stored `user_query` row, used when the user edits the last
 * question and regenerates its answer. Only `content` changes, and
 * `attachments` when the edit replaced them (omitted keeps the stored files);
 * mentions and seq stay as they were. Scoped to the conversation, so an id
 * from anywhere else matches nothing. The row gets a fresh change stamp, so a
 * reader that already holds it learns of the edit.
 */
export const updateUserQueryContent = async (
  messageId: mongoose.Types.ObjectId | string,
  content: string,
  sessionId: mongoose.Types.ObjectId | string,
  orgId: mongoose.Types.ObjectId | string,
  mongoSession?: ClientSession | null,
  attachments?: IChatAttachmentRef[],
): Promise<IChatSessionMessageDocument | null> => {
  const changeRev = await reserveChangeRevs(sessionId, 1, mongoSession);
  return ChatSessionMessage.findOneAndUpdate(
    {
      _id: messageId,
      orgId,
      sessionId,
      messageType: 'user_query',
      ...NOT_REMOVED,
    },
    {
      $set: { content, changeRev, ...(attachments ? { attachments } : {}) },
    },
    { returnDocument: 'after', session: mongoSession || undefined },
  );
};

export const hasAskUserQuestionTool = (msg: IMessage | undefined): boolean =>
  Boolean(
    msg?.tools?.some((tool) => tool.toolName?.includes('ask_user_question')),
  );

/**
 * The `ask_user_question` `tool_call` rows sitting either side of one bot turn,
 * which a regeneration of that turn makes stale.
 *
 * `updateMessageById` replaces the answer in place, but those rows are separate
 * documents and `appendMessages` can only add more (a fresh `seq` each time).
 * Left alone, every regeneration of a card turn keeps the discarded run's
 * questions, so `formatPreviousConversations` replays them to the model and a
 * reload restores a card the current answer never asked. The replacement answer
 * carries the payload itself — see `attachAskUserQuestionToMessage`.
 *
 * @param messages chronological (`seq`-ascending) window containing the turn
 */
export const staleAskUserQuestionToolCallIds = (
  messages: Array<IMessage & { _id?: { toString(): string } | string }>,
  botMessageId: { toString(): string } | string,
): string[] => {
  const botIndex = messages.findIndex(
    (msg) => msg._id?.toString() === botMessageId.toString(),
  );
  if (botIndex < 0) {
    return [];
  }
  const stale: string[] = [];
  const collect = (from: number, step: number): void => {
    for (let i = from; i >= 0 && i < messages.length; i += step) {
      const row = messages[i];
      if (row?.messageType !== 'tool_call') break;
      if (row._id && hasAskUserQuestionTool(row)) stale.push(String(row._id));
    }
  };
  collect(botIndex - 1, -1);
  collect(botIndex + 1, 1);
  return stale;
};

/**
 * Changes some fields of one row of `sessionId` in place and gives it a fresh
 * change stamp. `filter` names the row; any other condition in it must still
 * hold. False when no row matched; a removed row never does. `unset` names
 * fields the same write removes.
 */
export const setMessageFields = async (
  sessionId: mongoose.Types.ObjectId | string,
  filter: QueryFilter<IChatSessionMessageDocument>,
  fields: Record<string, unknown>,
  mongoSession?: ClientSession | null,
  unset: readonly string[] = [],
): Promise<boolean> => {
  const changeRev = await reserveChangeRevs(sessionId, 1, mongoSession);
  const result = await ChatSessionMessage.updateOne(
    { ...filter, sessionId, ...NOT_REMOVED },
    {
      $set: { ...fields, changeRev },
      ...(unset.length > 0 && {
        $unset: Object.fromEntries(unset.map((field) => [field, 1])),
      }),
    },
    { session: mongoSession ?? undefined, runValidators: true },
  );
  return result.matchedCount > 0;
};

/**
 * Removes one row: it stays as an emptied, stamped marker. An answer's label is archived with the time it was
 * retired, and the row gets a new version with no label.
 */
const retireMessage = async (
  _id: mongoose.Types.ObjectId,
  scope: { sessionId: mongoose.Types.ObjectId; orgId: mongoose.Types.ObjectId },
  emptied: Record<string, unknown>,
  removedAt: Date,
  mongoSession?: ClientSession | null,
): Promise<boolean> => {
  const target = { _id, orgId: scope.orgId };
  for (let attempt = 1; ; attempt += 1) {
    const stored = await readWithLabel(
      { ...target, sessionId: scope.sessionId, ...NOT_REMOVED },
      mongoSession,
    );
    if (!stored) return false;
    const labelHistory = archivedLabelHistory(stored, removedAt);
    if (!labelHistory) {
      return setMessageFields(scope.sessionId, target, emptied, mongoSession);
    }
    const retired = await setMessageFields(
      scope.sessionId,
      {
        ...target,
        ...(attempt < LABEL_WRITE_ATTEMPTS && sameLabelVersion(stored)),
      },
      { ...emptied, labelVer: newObjectId(), labelHistory },
      mongoSession,
      ['provenance'],
    );
    if (retired || attempt >= LABEL_WRITE_ATTEMPTS) return retired;
  }
};

/**
 * Removes rows of one session: each stays as an emptied, stamped marker, so a
 * reader that already holds it learns it is gone. Returns how many it removed.
 */
export const retireMessagesById = async (
  messageIds: mongoose.Types.ObjectId[],
  scope: { sessionId: mongoose.Types.ObjectId; orgId: mongoose.Types.ObjectId },
  mongoSession?: ClientSession | null,
  removedBy?: mongoose.Types.ObjectId,
): Promise<number> => {
  const removedAt = new Date();
  const emptied = {
    removedAt,
    ...(removedBy && { removedBy }),
    content: '',
    tools: [],
    parts: [],
    citations: [],
  };
  let retired = 0;
  for (const _id of messageIds) {
    if (await retireMessage(_id, scope, emptied, removedAt, mongoSession)) {
      retired += 1;
    }
  }
  return retired;
};

/**
 * Records one user's feedback on a message, replacing any entry they gave
 * before, in one atomic update. Not change-stamped: a reader only ever sees
 * their own vote.
 */
export const appendMessageFeedback = async (
  messageId: mongoose.Types.ObjectId | string,
  orgId: mongoose.Types.ObjectId | string,
  feedbackEntry: unknown,
  mongoSession?: ClientSession | null,
): Promise<IChatSessionMessageDocument | null> => {
  // An update pipeline skips schema casting and validation, so the entry gets them here.
  const row = new ChatSessionMessage({ feedback: [feedbackEntry] });
  // eslint-disable-next-line @typescript-eslint/no-deprecated -- a synchronous schema check, without middleware
  const invalid = row.validateSync(['feedback']);
  if (invalid) {
    throw invalid;
  }
  const entry = (
    row.feedback?.[0] as unknown as mongoose.Document
  ).toObject() as IFeedback;
  return ChatSessionMessage.findOneAndUpdate(
    { _id: messageId, orgId },
    [
      {
        $set: {
          feedback: {
            $concatArrays: [
              {
                $filter: {
                  input: { $ifNull: ['$feedback', []] },
                  cond: {
                    $ne: ['$$this.feedbackProvider', entry.feedbackProvider],
                  },
                },
              },
              // Literal, so text that looks like `$field` is stored, never evaluated.
              { $literal: [entry] },
            ],
          },
        },
      },
    ],
    {
      returnDocument: 'after',
      updatePipeline: true,
      session: mongoSession ?? undefined,
    },
  );
};

/** How far back regenerate looks for the conversation's last answer (question rows may follow it). */
export const REGENERATE_TAIL_MESSAGES = 50;

/**
 * Fetch a session's messages, `seq`-ordered (ascending = chronological,
 * matching the old embedded array's order). `.lean()`, matching every
 * existing read path. Short-circuits to `[]` when `limit <= 0` — MongoDB's
 * own `.limit(0)` means "no limit", the opposite of what a zero-message
 * page must return.
 */
export const getMessages = async (
  sessionId: mongoose.Types.ObjectId | string,
  options: {
    orgId: mongoose.Types.ObjectId | string;
    skip?: number;
    limit?: number;
    populateCitations?: boolean;
    sort?: 1 | -1;
    /** A durable answer's label (P2.5a) is never read unless asked for by name: it names sources a reader may not see. */
    withLabels?: boolean;
  },
  // A driver session, or the unit-of-work handle the harness's stores pass.
  transaction?: ClientSession | Tx | null,
): Promise<any[]> => {
  const {
    orgId,
    skip = 0,
    limit,
    populateCitations = false,
    sort = 1,
    withLabels = false,
  } = options;
  const mongoSession =
    transaction && 'store' in transaction
      ? sessionOf(transaction)
      : transaction;
  if (limit !== undefined && limit <= 0) {
    return [];
  }
  let query = ChatSessionMessage.find({
    sessionId,
    orgId,
    ...NOT_REMOVED,
  }).sort({ seq: sort });
  if (skip) {
    query = query.skip(skip);
  }
  if (limit !== undefined) {
    query = query.limit(limit);
  }
  if (populateCitations) {
    query = query.populate({
      path: 'citations.citationId',
      model: 'citation',
      select: '-__v',
    });
  }
  if (withLabels) {
    query = query.select('+label') as typeof query;
  }
  if (mongoSession) {
    query = query.session(mongoSession);
  }
  return query.lean().exec();
};

/**
 * Reconstruct the legacy `{...session, messages: [...]}` response shape
 * from a session object and an already-fetched messages array. Pure and
 * synchronous — this is the structural leakage guard for in-memory
 * `.toObject()` results that never passed through a query projection:
 * strips `nextSeq`/`sessionType` from the session and `sessionId`/`orgId`/
 * `seq` from each message, neither of which is part of any documented
 * response shape, and (via `withoutErrorStacks`) the server stack trace from
 * each `conversationErrors` entry.
 */
const withoutStack = (entry: unknown): unknown => {
  if (entry === null || typeof entry !== 'object') return entry;
  const { stack: _stack, ...rest } = entry as Record<string, unknown>;
  return rest;
};

/**
 * A copy of a conversation fit to send to the browser: each saved
 * `conversationErrors` entry keeps its message but loses the server stack
 * trace, which stays in the database for the logs and admins. Every
 * response that carries a whole conversation goes through this.
 */
export const withoutErrorStacks = <T extends object>(conversation: T): T => {
  const conversationErrors: unknown = (
    conversation as { conversationErrors?: unknown }
  ).conversationErrors;
  if (!Array.isArray(conversationErrors)) return conversation;
  return {
    ...conversation,
    conversationErrors: conversationErrors.map(withoutStack),
  };
};

/** Erasure bookkeeping (E7B) is not part of a conversation's response shape. */
export const withoutErasureMarker = <T extends object>(
  conversation: T,
): Omit<T, 'erasureRequestedAt'> => {
  const { erasureRequestedAt: _marker, ...rest } = conversation as T & {
    erasureRequestedAt?: unknown;
  };
  return rest;
};

/**
 * Error text, the failure reason and the compacted summary are written from the
 * owner's runs and can quote what they read; other people listing the chat get none.
 * A pin is the owner's own sidebar order, so it is theirs alone too.
 */
export const withoutOwnerOnlyFields = <T extends object>(
  conversation: T,
): T => {
  const {
    conversationErrors: _errors,
    failReason: _reason,
    failedRunBy: _failedBy,
    compactedSummary: _summary,
    isPinned: _pinned,
    ...visible
  } = conversation as Record<string, unknown>;
  return visible as T;
};

/**
 * The conversation the stream of a run ends with, as the person watching it may
 * read it: with the hiding a fetch applies (the watcher's own earlier answers are
 * checked too) and, for anyone but the conversation's owner, without the
 * owner-only fields. `ownAnswer`: the watcher's own run just produced it, so it
 * is not withheld.
 */
export const redactRunConversationFor = async (
  appConfig: AppConfig,
  viewer: ReturnType<typeof viewerOf>,
  conversation: Record<string, unknown>,
  opts: {
    ownAnswer?: string;
    citationsDecided: boolean;
    checkFailed: boolean;
  },
): Promise<Record<string, unknown>> => {
  const owner = String(conversation.initiator);
  const messages = (conversation.messages ?? []) as ViewerMessage[];
  const ownerOf = (m: ViewerMessage): string => messageActor(m, owner);
  return {
    ...(owner === viewer.userId
      ? conversation
      : { ...withoutOwnerOnlyFields(conversation), sharedWith: undefined }),
    messages: await redactMessagesFor(appConfig, viewer, messages, ownerOf, {
      fresh: (m) => opts.ownAnswer !== undefined && m.runId === opts.ownAnswer,
      ...(opts.citationsDecided && { citationsDecided: () => true }),
      checkFailed: opts.checkFailed,
    }),
  };
};

/**
 * A session without the fields its schema never selects (`select: false`): internal bookkeeping that no documented
 * response holds. A read leaves them out, but a session just created (a first send's) still holds every one.
 */
const withoutUnselectedFields = (session: object): Record<string, unknown> => {
  const unselected = new Set(
    Object.values(ChatSession.schema.paths)
      .filter((type) => (type.options as { select?: unknown }).select === false)
      .map((type) => type.path),
  );
  return Object.fromEntries(
    Object.entries(session).filter(([field]) => !unselected.has(field)),
  );
};

/**
 * `viewerId` is who the response goes to: each message passes through the message view extension for that viewer,
 * and the conversation through the conversation view unless `asStored` (a detail or list that shapes it itself).
 */
export const attachMessages = (
  session: any,
  messages: any[],
  viewerId?: string,
  { asStored = false }: { asStored?: boolean } = {},
): any => {
  const conversation = conversationExtensions().views.viewRunFailures(
    withoutErrorStacks(withoutUnselectedFields((session ?? {}) as object)),
    viewerId,
  );
  return {
    ...(asStored
      ? conversation
      : conversationExtensions().views.viewConversation(
          conversation,
          viewerId,
        )),
    messages: (messages || []).map((message: any) => {
      const {
        sessionId,
        orgId,
        seq,
        changeRev: _changeRev,
        ...rest
      } = message as Record<string, unknown>;
      const shaped =
        rest.parts === undefined
          ? rest
          : { ...rest, parts: withSavedEngineLines(rest.parts) };
      return sanitizeAnswerMessage(
        conversationExtensions().views.viewMessage(
          withoutLabelFields(shaped),
          viewerId,
        ),
      );
    }),
  };
};

/**
 * A conversation's rows on their way to `viewer`, with the sources other people's messages name (in their filters
 * and their text) as the viewer may see them, as the detail shows them.
 */
export const withViewerSources = async <C extends { messages?: unknown }>(
  conversation: C,
  viewer: { userId: string; orgId: string },
  stored?: readonly object[],
): Promise<C> => {
  if (
    !Array.isArray(conversation.messages) ||
    conversation.messages.length === 0
  ) {
    return conversation;
  }
  const chat = conversation as {
    userId?: { toString(): string } | null;
    initiator?: { toString(): string } | null;
  };
  const ownerId = String(chat.userId ?? chat.initiator ?? viewer.userId);
  return {
    ...conversation,
    messages: await conversationExtensions().views.viewSources(
      conversation.messages as object[],
      viewer,
      () => ownerId,
      stored,
    ),
  };
};

/**
 * A conversation on its way to `viewer` in a turn's frame or a JSON answer, as a fetch shows it: each answer judged
 * for them (`redactConversationFor`), then the sources other people's messages name.
 */
export const conversationForViewer = async <C extends { messages?: unknown }>(
  appConfig: AppConfig,
  viewer: { userId: string; orgId: string },
  conversation: C,
  opts?: Parameters<typeof redactConversationFor>[3],
): Promise<C> =>
  withViewerSources(
    await redactConversationFor(appConfig, viewer, conversation, opts),
    viewer,
    Array.isArray(conversation.messages)
      ? (conversation.messages as object[])
      : undefined,
  );

/** Who a turn's response frames are for: the run's requester, else the owner of a single-user chat. */
export const turnViewerId = (
  run: TurnRun | undefined,
  conversation: { userId?: { toString(): string } | null } | null | undefined,
): string | undefined =>
  run?.requestedBy?.toString() ?? conversation?.userId?.toString();

/**
 * Which rows a streamed turn's final frame carries. `'turn'`: only the rows the turn wrote, for a
 * client that merges them by id into the rows it has (it sends `frame=turn`). `'newest'`: the newest
 * `FULL_FRAME_MAX_ROWS` rows, for a client that replaces its whole list with the frame.
 */
export type StreamFrame = 'turn' | 'newest';

/** The most rows a final frame reads back, so an answer in a long chat never reads the whole chat. */
export const FULL_FRAME_MAX_ROWS = 1000;

/** The frame a streaming request asked for. */
export const streamFrameOf = (req: { query?: unknown }): StreamFrame =>
  (req.query as { frame?: unknown } | undefined)?.frame === 'turn'
    ? 'turn'
    : 'newest';

/** The rows a frame reads back, oldest first: the newest `FULL_FRAME_MAX_ROWS` for `'newest'`, else all of them. */
const framedMessages = async (
  sessionId: mongoose.Types.ObjectId | string,
  orgId: mongoose.Types.ObjectId | string,
  frame: StreamFrame | undefined,
  populateCitations: boolean,
  mongoSession?: ClientSession | null,
): ReturnType<typeof getMessages> =>
  frame === 'newest'
    ? (
        await getMessages(
          sessionId,
          { orgId, populateCitations, sort: -1, limit: FULL_FRAME_MAX_ROWS },
          mongoSession,
        )
      ).reverse()
    : getMessages(sessionId, { orgId, populateCitations }, mongoSession);

/**
 * Attach populated citation documents across ALL messages of a session.
 *
 * Earlier implementations built a lookup map from ONLY the newly-created
 * citations for the current response and then applied it to every message in
 * the conversation. That wiped `citationData` for previously-saved assistant
 * messages because their citationIds were not in the new-citations map — which
 * is exactly what caused inline citation chips in earlier answers to collapse
 * to unclickable numbered badges (no filename, no popover) on the client after
 * any follow-up query, only recovering on a full page refresh (the GET
 * `getConversationById` path correctly populates citations).
 *
 * Strategy:
 *   1. Re-fetch ALL of the session's messages with `populate` on every
 *      citationId (matches the GET path).
 *   2. For each message citation, if populate resolved to a full Citation
 *      document, use it. Otherwise fall back to the newly-created
 *      `fallbackCitations` array (handles transactional edge cases where the
 *      just-saved citation isn't visible to a follow-up query).
 *
 * `fallbackMessages` (typically just the message(s) the caller had in hand
 * from the write it just performed) is used instead of the fresh fetch when
 * Mongoose isn't connected (unit tests — see the readyState guard below) or
 * the fetch throws; it will not include older history in that case, which is
 * an accepted gap for the disconnected/test-only path (see the Phase 1
 * plan's "Known Phase 2 items").
 *
 * `frame` is for a streamed turn: `'newest'` reads only the newest rows, and
 * `'turn'` reads nothing, so the frame is `fallbackMessages` (the turn's own
 * rows) with the in-memory citations, marked `frame: 'turn'`.
 */
export const attachPopulatedCitations = async (
  session: any,
  fallbackMessages: any[],
  fallbackCitations: ICitation[],
  mongoSession?: ClientSession | null,
  viewerId?: string,
  frame?: StreamFrame,
): Promise<any> => {
  const sessionId = session?._id;
  let messages = fallbackMessages;

  // Only attempt the populate round-trip when Mongoose is actually connected.
  // In unit tests (and any environment without an active DB connection) the
  // default Mongoose buffering would hang this call for ~10s before failing,
  // which is both slow and unnecessary — the fallback branch handles those
  // cases using the caller-supplied messages.
  const isConnected =
    (mongoose.connection as mongoose.Connection | undefined)?.readyState ===
    mongoose.ConnectionStates.connected;

  if (sessionId && isConnected && frame !== 'turn') {
    try {
      messages = await framedMessages(
        sessionId,
        (session as { orgId: string }).orgId,
        frame,
        true,
        mongoSession,
      );
    } catch (err: any) {
      logger.warn(
        'Failed to populate citations for conversation response; falling back to newly-created citations only',
        { conversationId: sessionId?.toString(), error: err?.message },
      );
    }
  }

  const attached = attachMessages(session, messages, viewerId);
  return {
    ...attached,
    ...(frame === 'turn' && { frame }),
    messages: attached.messages.map((message: IMessage) => ({
      ...message,
      citations:
        message.citations?.map((citation: IMessageCitation) => {
          // After populate, `citationId` is the full Citation document;
          // otherwise it's still an ObjectId / string reference. We must
          // explicitly exclude ObjectId here because some bson versions
          // expose inherited properties that make a plain `'_id' in x`
          // check truthy on an ObjectId.
          const populated = citation.citationId as unknown as
            | (ICitation & { _id?: mongoose.Types.ObjectId })
            | mongoose.Types.ObjectId
            | string
            | undefined;
          const isPopulatedCitationDoc =
            !!populated &&
            typeof populated === 'object' &&
            !(populated instanceof mongoose.Types.ObjectId) &&
            (populated as any)._bsontype !== 'ObjectId' &&
            '_id' in populated;
          if (isPopulatedCitationDoc) {
            const doc = populated;
            return {
              ...citation,
              citationId: doc._id,
              citationData: doc,
            };
          }
          // Fallback to the newly-created citations for this response.
          return {
            ...citation,
            citationData: citation.citationId
              ? fallbackCitations.find(
                  (c: ICitation) =>
                    c._id?.toString() === citation.citationId?.toString(),
                )
              : undefined,
          };
        }) || [],
    })),
  };
};

export const isClassifiedFailureAnswer = (
  data: Pick<IAIResponse, 'answerMatchType'> & { errorCode?: string },
): boolean =>
  data.answerMatchType === 'Error' ||
  (typeof data.errorCode === 'string' && data.errorCode.length > 0);

/** The status (and failure record) a finished run leaves on its session. */
export const classifiedFailureChange = (
  completeData: IAIResponse,
): Pick<RunStateChange, 'status' | 'failReason' | 'appendError'> => {
  if (completeData.status === 'stopped') {
    return { status: CONVERSATION_STATUS.STOPPED };
  }
  if (!isClassifiedFailureAnswer(completeData)) {
    return { status: CONVERSATION_STATUS.COMPLETE };
  }
  const code = completeData.errorCode || 'unknown_error';
  return {
    status: CONVERSATION_STATUS.FAILED,
    failReason: completeData.answer,
    appendError: buildSessionError(
      completeData.answer,
      code,
      undefined,
      undefined,
      new Map<string, unknown>([
        ['type', AGUIEventType.RUN_FINISHED],
        ['code', code],
      ]),
    ),
  };
};

export const recordClassifiedFailureOnSession = (
  conversation: IChatSessionDocument,
  completeData: IAIResponse,
  run?: TurnRun,
): void => {
  if (completeData.status === 'stopped') {
    conversation.status = CONVERSATION_STATUS.STOPPED;
    return;
  }
  if (!isClassifiedFailureAnswer(completeData)) {
    conversation.status = CONVERSATION_STATUS.COMPLETE;
    return;
  }
  const code = completeData.errorCode || 'unknown_error';
  conversation.status = CONVERSATION_STATUS.FAILED;
  conversation.failedRunBy = askerOfTurn(run, conversation);
  if (!failsOnConversation(run, conversation)) return;
  conversation.failReason = completeData.answer;
  addErrorToConversation(
    conversation,
    completeData.answer,
    code,
    undefined,
    undefined,
    new Map<string, unknown>([
      ['type', AGUIEventType.RUN_FINISHED],
      ['code', code],
    ]),
    askerOfTurn(run, conversation),
  );
};

/**
 * The model an answer came from: the one the AI service reports, else the
 * request's. The request's `modelInfo` is kept for the conversation, never
 * mutated here.
 */
export const answeredModelInfo = (
  requested: IAIModel | undefined,
  // From the AI service's JSON, so any field can be missing.
  answered: Partial<IAIModel> | undefined,
): IAIModel | undefined => {
  if (!answered?.modelKey) {
    return requested;
  }
  const effort = answered.reasoningEffort;
  return {
    modelKey: answered.modelKey,
    modelName: answered.modelName ?? '',
    // The request's provider belongs to the requested model, which may not be this one.
    modelProvider: answered.modelProvider ?? '',
    chatMode: requested?.chatMode ?? 'quick',
    ...(answered.modelFriendlyName && {
      modelFriendlyName: answered.modelFriendlyName,
    }),
    ...(effort &&
      (REASONING_EFFORT_VALUES as readonly string[]).includes(effort) && {
        reasoningEffort: effort,
      }),
    ...(typeof answered.modelNote === 'string' &&
      answered.modelNote && { modelNote: answered.modelNote.slice(0, 200) }),
  };
};

/** The limit that cut an answer short (BUDGETEND), when the completion names one this build knows. */
const cutShortBy = (
  data: IAIResponse,
): Pick<IMessage, 'truncatedBy' | 'budgetScope'> =>
  !isClassifiedFailureAnswer(data) &&
  typeof data.truncatedBy === 'string' &&
  (CUT_SHORT_REASONS as readonly string[]).includes(data.truncatedBy)
    ? {
        truncatedBy: data.truncatedBy as CutShortReason,
        // C9.06 (WP-51): which shared budget ran out, so an extension's stop line can name it.
        ...budgetScopeField(data as unknown as Record<string, unknown>),
      }
    : {};

export const buildAIResponseMessage = (
  aiResponse: AIServiceResponse<IAIResponse>,
  // Saved citation documents, or the repository's (the harness's writers): each answer row names them by id as given.
  citations: ReadonlyArray<Pick<ICitation, '_id'> | { _id: string }> = [],
  modelInfo?: IAIModel,
): IMessage => {
  const data = aiResponse?.data;
  // A `stopped` run may have been cancelled before any tokens streamed —
  // an empty answer is valid there (see AnswerFinalizer's cancelled branch).
  // `waiting_input` is the ask_user_question pause: the card is the turn,
  // not a prose answer.
  // WP-89: a run whose outcome the web app words (a file it still owes, nothing gathered) may have no prose.
  const allowsEmptyAnswer =
    data?.status === 'stopped' ||
    data?.status === 'waiting_input' ||
    data?.runOutcome !== undefined;
  if (!data || (!data.answer && !allowsEmptyAnswer)) {
    throw new InternalServerError('AI response must include an answer');
  }

  const { errorCode } = data as { errorCode?: string };
  const message: IMessage = {
    messageType: isClassifiedFailureAnswer(data) ? 'error' : 'bot_response',
    // An error answer keeps its code for the web app to word (ERR-UX); a refusal partway through a run reads as one
    // before it does: trying again cannot help.
    ...((isClassifiedFailureAnswer(data) && errorCode) ||
    adminRefusals().isCode(errorCode)
      ? { errorCode }
      : {}),
    createdAt: new Date(),
    updatedAt: new Date(),
    content: data.answer ?? '',
    contentFormat: 'MARKDOWN',
    citations: citations.map((citation) => ({
      citationId: citation._id as ICitation['_id'],
    })),
    confidence: data.confidence,
    ...(data.answerMatchType === 'Capability Card'
      ? { answerMatchType: 'Capability Card' as const }
      : {}),
    ...cutShortBy(data),
    ...(data.runOutcome !== undefined ? { runOutcome: data.runOutcome } : {}),
    ...notFoundOf(data as unknown as Record<string, unknown>),
    ...couldNotDoOf(data as unknown as Record<string, unknown>),
    followUpQuestions:
      data.followUpQuestions?.map((q) => ({
        question: q.question,
        confidence: q.confidence,
        reasoning: q.reasoning,
      })) || [],
    metadata: {
      processingTimeMs: data.metadata?.processingTimeMs,
      modelVersion: data.metadata?.modelVersion,
      aiTransactionId: data.metadata?.aiTransactionId,
      reason: data.reason,
    },
    modelInfo: answeredModelInfo(modelInfo, data.modelInfo),
  };

  // Include referenceData if present (IDs for follow-up queries)
  // This stores technical IDs that were in the response for later reference
  // Filter out invalid items (must have name and at least key or id)
  if (data.referenceData && Array.isArray(data.referenceData)) {
    message.referenceData = data.referenceData.filter((item) => {
      // Ensure item has name and at least one of key or id (id can be optional)
      return item?.name;
    });
  }

  // Present only when PIPESHUB_PERSIST_REASONING=true on the Python side
  // (see reasoning_persistence.py) — absent for every existing client/run.
  if (
    data.reasoning &&
    Array.isArray(data.reasoning) &&
    data.reasoning.length > 0
  ) {
    message.reasoning = data.reasoning;
  }

  // Ordered agent-activity transcript (`agui` protocol only — see
  // TranscriptCollector/respond.py) — absent for the legacy protocol and
  // for every pre-existing conversation. Copied through as-is: Python has
  // already bounded every field (tool args/result previews, truncated
  // reasoning) before this reaches Node, so no full external tool result
  // ever lands in Mongo via this path.
  if (data.parts && Array.isArray(data.parts) && data.parts.length > 0) {
    message.parts = data.parts;
  }

  const unavailableTools = sanitizeUnavailableTools(data.unavailableTools);
  if (unavailableTools.length > 0) {
    message.unavailableTools = unavailableTools;
  }

  if (data.status === 'stopped') {
    message.status = 'stopped';
  }

  // The save binds it to the stored version and text (`sealAnswerForStorage`); without one, only the asker sees it.
  const label = answerLabelFrom(data.provenance);
  if (label) {
    message.provenance = label;
  }

  // FG2: what the turn read, so the next turn's flow guard (and a durable import) can decide from it.
  const turnLabel = parseTurnLabel(data.label);
  if (turnLabel !== null) {
    message.label = turnLabel;
  }

  return message;
};

/**
 * Stamp the questions payload onto the regenerated bot row itself.
 *
 * Regeneration replaces the bot message wholesale (see updateMessageById), so
 * the `tool_call` row saved alongside it is the only other copy — carrying it
 * here as well means a reload restores the card even if that sibling row is
 * missing (older conversations, a failed append).
 */
export const attachAskUserQuestionToMessage = (
  message: IMessage,
  payload: unknown,
): IMessage => {
  if (!payload || typeof payload !== 'object') {
    return message;
  }
  const existingTools = message.tools ?? [];
  message.tools = [
    ...existingTools.filter(
      (tool) => !tool.toolName?.includes('ask_user_question'),
    ),
    { toolName: 'ask_user_question', toolResult: payload },
  ];
  return message;
};

// Reconstructs a bot turn's tool activity for `previousConversations[i].
// tool_results`, in the exact shape `_convert_conversation_turn`
// (factory.py) already parses (`tool_id`/`tool_name`/`args`/`result`/
// `status`). Sourced from the already-persisted, already-bounded `parts`
// transcript (see `messageSchema.parts` — the Python `TranscriptCollector`
// caps every field before it ever reaches Mongo) rather than reviving a
// full-payload tool-results field: resending untruncated external tool
// output over the wire is exactly what `_tool_names_from_state` (Python,
// agent_loop/respond.py) deliberately stopped doing.
const toolResultsFromParts = (parts?: IMessagePart[]): PreviousToolResult[] => {
  if (!parts || parts.length === 0) {
    return [];
  }
  return parts
    .filter((part) => part.type === 'tool_call' && part.toolName)
    .map((part) => {
      const replaced = conversationExtensions().history.toolResult(part);
      if (replaced !== undefined) {
        return replaced;
      }
      let args: Record<string, unknown> | undefined;
      if (part.args) {
        try {
          const parsed = JSON.parse(part.args);
          if (parsed && typeof parsed === 'object') {
            args = parsed as Record<string, unknown>;
          }
        } catch {
          // `args` wasn't a JSON object (already-summarized text) — the
          // consumer falls back to {} for non-dict args, which is fine:
          // the tool call's presence/result matters more than replaying
          // its exact arguments.
        }
      }
      return {
        tool_id: part.toolCallId,
        tool_name: part.toolName,
        ...(args && { args }),
        result: part.resultSummary || part.resultPreview || '',
        ...(part.resultSummary && { result_summary: part.resultSummary }),
        // A blocked call, or one still waiting for approval, never ran: replaying it as a
        // success would tell the next turn it had.
        status:
          part.status === 'failed' ||
          part.status === 'blocked' ||
          part.status === 'awaiting_approval'
            ? ('error' as const)
            : ('success' as const),
        ...(part.artifactId && { artifact_id: part.artifactId }),
      };
    });
};

const askToolResults = (msg: IMessage | undefined): PreviousToolResult[] => {
  if (!msg?.tools?.length) {
    return [];
  }
  return msg.tools
    .filter(
      (tool) => tool.toolName.includes('ask_user_question') && tool.toolResult,
    )
    .map((tool) => ({
      tool_id: 'ask_user_question',
      tool_name: tool.toolName.includes('internaltools')
        ? tool.toolName
        : 'internaltools__ask_user_question',
      result:
        typeof tool.toolResult === 'string'
          ? tool.toolResult
          : JSON.stringify(tool.toolResult),
      status: 'success' as const,
    }));
};

/**
 * The questions one bot turn asked, from whichever copy is newest.
 *
 * Regeneration stamps the payload on the answer itself; the live path saves it
 * as a `tool_call` row just before the answer. Trailing `tool_call` rows are
 * what regenerations appended before `staleAskUserQuestionToolCallIds` —
 * only the last of those is the turn's current question, the rest belong to
 * discarded runs.
 */
const askResultsForTurn = (
  messages: IMessage[],
  botIndex: number,
): PreviousToolResult[] => {
  const own = askToolResults(messages[botIndex]);
  if (own.length) return own;
  let trailing: PreviousToolResult[] = [];
  for (let j = botIndex + 1; j < messages.length; j++) {
    if (messages[j]?.messageType !== 'tool_call') break;
    const rows = askToolResults(messages[j]);
    if (rows.length) trailing = rows;
  }
  if (trailing.length) return trailing;
  return messages[botIndex - 1]?.messageType === 'tool_call'
    ? askToolResults(messages[botIndex - 1])
    : [];
};

/**
 * The questions a bot turn asked that its own `parts` do not already carry
 * (#3592: the questions row is a sibling of the answer; a resume needs it on
 * this turn).
 */
const turnAskToolResults = (
  messages: IMessage[],
  botIndex: number,
): PreviousToolResult[] => {
  const msg = messages[botIndex];
  if (msg?.messageType !== 'bot_response') return [];
  const own = toolResultsFromParts(msg.parts);
  if (own.some((row) => row.tool_name?.includes('ask_user_question'))) {
    return [];
  }
  return askResultsForTurn(messages, botIndex);
};

/** Why a history turn is not a whole answer: the user stopped it, or the turn failed before one. */
type HistoryTurnStatus = 'stopped' | 'failed';

/** One turn of the history sent to the AI service; a type alias, not an interface, so it stays assignable to a plain record. */
export type HistoryTurn = {
  content: IMessage['content'];
  role: IMessage['messageType'];
  sender?: string;
  attachments?: NonNullable<IMessage['attachments']>;
  referenceData?: NonNullable<IMessage['referenceData']>;
  tool_results?: PreviousToolResult[];
  status?: HistoryTurnStatus;
  modelKey?: string;
  modelName?: string;
  reasoningEffort?: IAIModel['reasoningEffort'];
  modelNote?: string;
};

const historyTurn = (
  msg: IMessage,
  parts: IMessagePart[] | undefined,
  askResults: PreviousToolResult[] = [],
): HistoryTurn => {
  const toolResults =
    msg.messageType === 'bot_response'
      ? [...toolResultsFromParts(parts), ...askResults]
      : [];
  return {
    content: msg.content,
    role: msg.messageType,
    // Whose turn it is when not the owner's (SS2): history read for another
    // actor stubs it.
    // A stored row may carry a null sender, which the message type does not admit.
    ...(msg.senderId != null && {
      sender: historySender(String(msg.senderId)),
    }),
    ...(msg.attachments &&
      msg.attachments.length > 0 && {
        attachments: msg.attachments,
      }),
    // Include referenceData for follow-up queries (IDs from tool responses)
    ...(msg.referenceData &&
      msg.referenceData.length > 0 && {
        referenceData: msg.referenceData,
      }),
    // Prior tool calls/results for this turn — lets the rebuilt agent
    // see HOW a past answer was produced instead of text-only history
    // (see `_convert_conversation_turn`, factory.py).
    ...(toolResults.length > 0 && { tool_results: toolResults }),
    // A reply the user stopped did not finish its card's work, so the card can be answered again.
    ...(msg.messageType === 'bot_response' &&
      msg.status === 'stopped' && { status: 'stopped' }),
    // Which model wrote each answer, for the AI service.
    ...(msg.messageType === 'bot_response' &&
      msg.modelInfo?.modelKey && {
        modelKey: msg.modelInfo.modelKey,
        modelName: msg.modelInfo.modelName,
        ...(msg.modelInfo.reasoningEffort && {
          reasoningEffort: msg.modelInfo.reasoningEffort,
        }),
        ...(msg.modelInfo.modelNote && {
          modelNote: msg.modelInfo.modelNote,
        }),
      }),
  };
};

/** Whether the turn at `index` still gets an answer before the next question. */
const answerFollows = (messages: IMessage[], index: number): boolean => {
  for (let j = index + 1; j < messages.length; j++) {
    const type = messages[j]?.messageType;
    if (type === 'bot_response') return true;
    if (type === 'user_query') return false;
  }
  return false;
};

/** A steering message the run took (P1b T2); one it never took reached no model. */
const isTakenInput = (part: IMessagePart): boolean =>
  part.type === 'user_input' && part.consumed !== false;

/**
 * What the model answered before a steering message: the segment's last text
 * when no tool call follows it. Narration before a tool call is left out, as
 * history leaves it out of every tool-calling turn.
 */
const segmentAnswer = (segment: IMessagePart[]): string => {
  let answer = '';
  for (const part of segment) {
    if (part.type === 'text') answer = part.content ?? '';
    else if (part.type === 'tool_call') answer = '';
  }
  return answer;
};

/**
 * One message's `previousConversations` entries (none for the types history
 * leaves out). A durable answer that took steering messages is split where it
 * took them, so a later model sees each one after the tool calls before it,
 * as the run's own model did.
 */
export const historyTurns = (
  msg: IMessage,
  askResults: PreviousToolResult[] = [],
): HistoryTurn[] => {
  if (msg.messageType === 'error' || msg.messageType === 'tool_call') {
    return [];
  }
  const parts = msg.parts;
  if (msg.messageType !== 'bot_response' || !parts?.some(isTakenInput)) {
    return [historyTurn(msg, parts, askResults)];
  }
  const turns: HistoryTurn[] = [];
  let segment: IMessagePart[] = [];
  for (const part of parts) {
    if (!isTakenInput(part)) {
      segment.push(part);
      continue;
    }
    const answered = segmentAnswer(segment);
    if (answered !== '' || toolResultsFromParts(segment).length > 0) {
      turns.push(
        historyTurn(
          { messageType: 'bot_response', content: answered },
          segment,
        ),
      );
    }
    turns.push({ content: part.content ?? '', role: 'user_query' });
    segment = [];
  }
  turns.push(historyTurn(msg, segment, askResults));
  return turns;
};

/** Rows the AI backend names as parents of the new answer when it is given their words or tool data. */
const ANSWER_ROLES: ReadonlySet<string> = new Set([
  'bot_response',
  'shared_answer',
]);

/**
 * A history row's `_ref` (its id), `_ver` (the version of its text; `'legacy'` for a row written before versions) and
 * `_pub` (that version restricts nobody). A row a check withheld goes with its mark and no seal, and an answer the
 * model is given nothing of (no text, tool data or references) goes with no seal: the seal names what the new answer
 * was given, and an id and version rebuilt for such a row would make the answer inherit a row it never saw.
 */
const sealOf = (
  msg: IMessage,
  givesData: boolean,
): { _ref?: string; _ver?: string; _pub?: boolean; [WITHHELD]?: true } => {
  if (isWithheld(msg)) return { [WITHHELD]: true };
  const row = msg as IMessage & {
    _id?: unknown;
    _ref?: unknown;
    _ver?: unknown;
    _pub?: unknown;
  };
  if (
    ANSWER_ROLES.has(msg.messageType) &&
    !givesData &&
    !(typeof msg.content === 'string' && msg.content.trim() !== '')
  ) {
    return {};
  }
  const ref = typeof row._ref === 'string' ? row._ref : idOfValue(row._id);
  if (ref === undefined || ref === '') return {};
  const ver =
    typeof row._ver === 'string'
      ? row._ver
      : typeof msg.labelVer === 'string' && msg.labelVer !== ''
        ? msg.labelVer
        : 'legacy';
  // Only the reader check says a version restricts nobody; a row it did not mark does.
  return { _ref: ref, _ver: ver, _pub: row._pub === true };
};

/**
 * Fields added to one history row, or `null` to leave the row out. Lets a caller attribute rows
 * (who asked, which agent answered) without changing the shape of the rest.
 */
export type PreviousTurnAnnotator = (
  message: IMessage,
) => Record<string, unknown> | null;

/**
 * One row's history entries as the reader check left the row: a row it withheld gives the model nothing (no text,
 * tool data, references or files) and goes as one entry; every entry carries the row's seal and the caller's fields.
 */
const checkedHistoryTurns = (
  messages: IMessage[],
  index: number,
  annotations: Record<string, unknown>,
): HistoryTurn[] => {
  const msg = messages[index] as IMessage;
  if (isWithheld(msg)) {
    const { attachments: _files, referenceData: _refs, ...rest } = msg;
    return [
      {
        ...historyTurn({ ...rest, content: '' }, undefined),
        ...sealOf(msg, false),
        ...annotations,
      },
    ];
  }
  return historyTurns(msg, turnAskToolResults(messages, index)).map((turn) => ({
    ...turn,
    // The row and the version of its text: the new answer's label names the earlier answers it was given by them.
    ...sealOf(
      msg,
      (turn.tool_results?.length ?? 0) > 0 ||
        (turn.referenceData?.length ?? 0) > 0,
    ),
    ...annotations,
  }));
};

/**
 * Prior turns for the AI service, oldest first. Legacy routes run the owner's
 * turns only: anyone else's turns reach the model as a stub (SS2).
 *
 * A turn that failed before an answer leaves an `error` row in place of one.
 * It is sent as an answer with `status: 'failed'`: dropped, its question
 * replayed as still open, and providers that merge consecutive user turns
 * joined it to the current question. An answer the user stopped carries
 * `status: 'stopped'`. The AI service renders both as short markers.
 */
export const formatPreviousConversations = (
  messages: IMessage[],
  annotate?: PreviousTurnAnnotator,
): HistoryTurn[] => {
  const turns: HistoryTurn[] = [];
  // Tracked, not read off the last entry: a fork keeps stored `system` rows,
  // which can sit between a question and its error.
  let openQuestion: IMessage | undefined;
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (!msg || msg.messageType === 'tool_call') {
      continue;
    }
    const annotations = annotate ? annotate(msg) : {};
    if (annotations === null) {
      continue;
    }
    if (msg.messageType === 'error') {
      if (openQuestion !== undefined && !answerFollows(messages, i)) {
        // The marker is the open question's answer, so it goes wherever that question's turn goes (SS2).
        const asker = openQuestion.senderId;
        turns.push({
          role: 'bot_response',
          content: '',
          status: 'failed',
          ...(asker != null && { sender: historySender(String(asker)) }),
        });
        openQuestion = undefined;
      }
      continue;
    }
    if (msg.messageType === 'user_query') openQuestion = msg;
    if (msg.messageType === 'bot_response') openQuestion = undefined;
    turns.push(...checkedHistoryTurns(messages, i, annotations));
  }
  return ownerHistoryTurns(turns) as HistoryTurn[];
};

export const getPaginationParams = (req: AuthenticatedUserRequest) => {
  try {
    // Validate and sanitize page and limit parameters for XSS

    if (req.query?.page) {
      validateNoXSS(req.query.page as string, 'page parameter');
    }
    if (req.query?.limit) {
      validateNoXSS(req.query.limit as string, 'limit parameter');
    }

    return safeParsePagination(
      req.query?.page as string | undefined,
      req.query?.limit as string | undefined,
      1,
      20,
      100,
    );
  } catch (error: any) {
    // Fallback to safe defaults if parsing fails
    return { page: 1, limit: 20, skip: 0 };
  }
};

export const buildSortOptions = (
  req: AuthenticatedUserRequest,
  { pinnedFirst = false }: { pinnedFirst?: boolean } = {},
) => {
  const allowedSortFields = ['createdAt', 'lastActivityAt', 'title'];
  const sortField = allowedSortFields.includes(req.query?.sortBy as string)
    ? (req.query?.sortBy as string)
    : 'lastActivityAt';

  return {
    // Before the page is cut, so every pinned chat is on page 1 and the
    // sidebar's Pinned group is never split across pages.
    ...(pinnedFirst ? { isPinned: -1 } : {}),
    [sortField]: req.query.sortOrder === 'asc' ? 1 : -1,
    _id: -1, // Secondary sort for consistency
  };
};

type SharedWithRow = {
  userId?: { toString(): string } | null;
  accessLevel?: string;
};

export const sharedWithUserId = (
  row: SharedWithRow | null | undefined,
): string | undefined => row?.userId?.toString();

/** Rows to persist: only null/non-object entries are dropped; rows without a userId (e.g. team rows) are kept untouched. */
export const persistableSharedWith = <T extends SharedWithRow>(
  rows: Array<T | null | undefined> | null | undefined,
): T[] =>
  (rows ?? []).filter(
    (row): row is T => row !== null && typeof row === 'object',
  );

/** Rows a user-level check can reason about; never use the result as the saved array. */
export const userSharedWithRows = <T extends SharedWithRow>(
  rows: Array<T | null | undefined> | null | undefined,
): T[] =>
  (rows ?? []).filter((row): row is T => sharedWithUserId(row) !== undefined);

export const sharedWithUserIdEquals = (
  row: SharedWithRow | null | undefined,
  userId: string,
): boolean => sharedWithUserId(row) === userId;

/**
 * What the reader of a conversation is to it (SS0): `role` is the explicit
 * meaning of `accessLevel` (`read` → viewer, `write` → collaborator); a reader
 * with no share (a project member) is a viewer. Only user rows count: a team
 * row names no user.
 */
export const viewerAccess = (
  conversation: {
    initiator: { toString(): string };
    isShared?: boolean;
    sharedWith?: SharedWithRow[];
  },
  userId: string | { toString(): string },
): { isOwner: boolean; accessLevel: string; role: SessionRole } => {
  // Callers may hand over an ObjectId where a string is typed.
  const viewerId = String(userId);
  const shares = userSharedWithRows(conversation.sharedWith).map((share) => ({
    userId: sharedWithUserId(share) ?? '',
    accessLevel: share.accessLevel ?? 'read',
  }));
  return {
    isOwner: conversation.initiator.toString() === viewerId,
    accessLevel:
      shares.find((share) => share.userId === viewerId)?.accessLevel || 'read',
    role:
      sessionRoleOf(
        {
          ownerId: conversation.initiator.toString(),
          isShared: conversation.isShared === true,
          shares,
        },
        viewerId,
      ) ?? 'viewer',
  };
};

/** With `access` the role comes from the guard's policy, so a write recipient or team member is not reported as `read`. */
export const addComputedFields = <
  T extends {
    initiator: { toString(): string };
    isShared?: boolean;
    sharedWith?: SharedWithRow[];
  },
>(
  conversation: T,
  userId: string,
  access?: AccessView,
) => {
  const isOwner = conversation.initiator.toString() === userId;
  // Owner-run fields are for the owner only, in every list a shared chat appears in.
  const cleaned = conversationExtensions().views.viewRunFailures(
    withoutErrorStacks(
      isOwner ? conversation : withoutOwnerOnlyFields(conversation),
    ),
    userId,
  );
  return {
    ...cleaned,
    ...('title' in cleaned && {
      title: conversationExtensions().views.displayTitle(
        cleaned.title as string | undefined,
      ),
    }),
    ...viewerAccess(conversation, userId),
    ...(access && { accessLevel: access.accessLevel, access }),
  };
};

/** Other recipients' identities are not part of a list row for someone the chat was shared with. */
export const withoutSharedWith = <T extends { sharedWith?: unknown }>(
  row: T,
): Omit<T, 'sharedWith'> => {
  const copy = { ...row };
  delete copy.sharedWith;
  return copy;
};

const userDirectory = new MongoUserDirectory();

export type SharedByInfo = {
  userId: string;
  name: string;
};

function conversationIsOwnedByCaller(conversation: {
  isOwner?: boolean;
  access?: { isOwner?: boolean };
}): boolean {
  return conversation.isOwner === true || conversation.access?.isOwner === true;
}

/** Resolve initiator IDs to display names for recipients (initiator is the sharer). */
export const attachSharedBy = async <
  T extends {
    initiator?: { toString(): string };
    isOwner?: boolean;
    access?: { isOwner?: boolean };
  },
>(
  conversations: T[],
  orgId: string,
): Promise<Array<T & { sharedBy?: SharedByInfo }>> => {
  if (conversations.length === 0) {
    return conversations;
  }

  const recipientConversations = conversations.filter(
    (conversation) => !conversationIsOwnedByCaller(conversation),
  );

  const initiatorIds = [
    ...new Set(
      recipientConversations
        .map((conversation) => conversation.initiator?.toString())
        .filter((id): id is string => {
          if (!id) return false;
          return mongoose.Types.ObjectId.isValid(id);
        }),
    ),
  ];

  if (initiatorIds.length === 0) {
    return conversations;
  }

  const namesById = await userDirectory.displayNames(orgId, initiatorIds);

  return conversations.map((conversation) => {
    const initiatorId = conversation.initiator?.toString();
    if (!initiatorId) {
      return conversation;
    }
    if (conversationIsOwnedByCaller(conversation)) {
      return conversation;
    }
    const name = namesById.get(initiatorId);
    const sharedBy: SharedByInfo = {
      userId: initiatorId,
      name: name || initiatorId,
    };
    return { ...conversation, sharedBy };
  });
};
export const attachSharedByIfRecipient = async <
  T extends {
    initiator?: { toString(): string };
    access?: { isOwner?: boolean };
  },
>(
  conversation: T,
  orgId: string | undefined,
): Promise<T & { sharedBy?: SharedByInfo }> => {
  if (!orgId || conversation.access?.isOwner) {
    return conversation;
  }
  const [enriched] = await attachSharedBy([conversation], orgId);
  return enriched ?? conversation;
};

/**
 * Base access filter for chat sessions / enterprise searches in list and
 * by-id flows. Matches either:
 * - rows owned by this user (`userId`), or
 * - rows explicitly shared with this user (`isShared` and `sharedWith` contains
 *   their id).
 *
 * The shared branch uses `$and` so `isShared: true` alone does not grant access.
 *
 * `contentMatchIds`, when provided, ORs an `_id: {$in: ...}` clause into the
 * search predicate alongside the title regex — see
 * `findSessionIdsMatchingContent`. Callers that don't pass it (the 8
 * `EnterpriseSemanticSearch` call sites, whose documents have no `messages`)
 * get exactly today's title-only search behaviour.
 */
/** Sentinel accepted by `?projectId=` to mean "sessions with no project link". */
export const PROJECT_ID_UNASSIGNED_QUERY_VALUE = 'unassigned';

type QueryParts = Pick<
  SessionListQuery,
  'search' | 'startDate' | 'endDate' | 'isSharedFilter' | 'projectFilter'
>;

/**
 * The request-derived part of a session list query: the `?projectId=`,
 * `?search=`, `?startDate=`/`?endDate=` and `?shared=` parameters, checked
 * in the order they always were. A malformed projectId only narrows a list,
 * so it is ignored rather than rejected.
 */
export const parseSessionQueryParams = (
  req: AuthenticatedUserRequest,
  options: { formatSpecifiers?: boolean } = {},
): QueryParts => {
  const out: QueryParts = {};
  const projectIdRaw = req.query?.projectId;
  if (typeof projectIdRaw === 'string' && projectIdRaw.length > 0) {
    if (projectIdRaw === PROJECT_ID_UNASSIGNED_QUERY_VALUE) {
      out.projectFilter = { kind: 'unassigned' };
    } else if (isValidId(projectIdRaw)) {
      out.projectFilter = { kind: 'project', id: projectIdRaw };
    }
  }
  // Handle search with XSS validation
  if (req.query.search) {
    out.search = validateAndEscapeSearch(req.query.search, options);
  }
  // Handle date range
  if (req.query.startDate) {
    const startDate = new Date(req.query.startDate as string);
    if (isNaN(startDate.getTime())) {
      throw new BadRequestError('Invalid start date format');
    }
    out.startDate = startDate;
  }
  if (req.query.endDate) {
    const endDate = new Date(req.query.endDate as string);
    if (isNaN(endDate.getTime())) {
      throw new BadRequestError('Invalid end date format');
    }
    out.endDate = endDate;
  }
  // Handle shared/private filter with XSS validation
  if (req.query.shared !== undefined) {
    const sharedValue = validateBooleanParam(
      req.query.shared as string,
      'shared parameter',
    );
    if (sharedValue !== undefined) {
      out.isSharedFilter = sharedValue;
    }
  }
  return out;
};

/** What `buildFiltersMetadata` reads from an applied filter: only the date range. */
export const dateRangeFilterOf = (
  q: Pick<SessionListQuery, 'startDate' | 'endDate'>,
): { createdAt?: { $gte?: Date; $lte?: Date } } => {
  if (!q.startDate && !q.endDate) return {};
  return {
    createdAt: {
      ...(q.startDate ? { $gte: q.startDate } : {}),
      ...(q.endDate ? { $lte: q.endDate } : {}),
    },
  };
};

/** A session list query from the request plus what the handler decided. */
export const buildSessionQuery = (
  req: AuthenticatedUserRequest,
  base: Omit<
    SessionListQuery,
    'search' | 'startDate' | 'endDate' | 'isSharedFilter' | 'projectFilter'
  >,
  options: { formatSpecifiers?: boolean } = {},
): SessionListQuery => {
  if (base.scope.kind === 'chat' && !base.owned && !base.shared) {
    throw new BadRequestError('Either owned or shared must be true');
  }
  return { ...base, ...parseSessionQueryParams(req, options) };
};

/**
 * AND-composes an optional `?projectId=<id>|unassigned` query filter onto an
 * existing chatSessions filter object, mutating it in place. Shared by
 * `buildFilter` so the list/detail endpoints support the same query
 * contract. A malformed (non-ObjectId, non-'unassigned') value is ignored
 * rather than thrown, since it only narrows a list — never called on a
 * `require`d id param.
 */
export const applyProjectIdQueryFilter = (
  filter: Record<string, unknown>,
  req: AuthenticatedUserRequest,
): void => {
  const projectIdRaw = req.query?.projectId;
  if (typeof projectIdRaw !== 'string' || projectIdRaw.length === 0) {
    return;
  }
  if (projectIdRaw === PROJECT_ID_UNASSIGNED_QUERY_VALUE) {
    filter.projectId = { $exists: false };
    return;
  }
  if (mongoose.Types.ObjectId.isValid(projectIdRaw)) {
    filter.projectId = new mongoose.Types.ObjectId(projectIdRaw);
  }
};

const dateRangeConstraint = (
  req: AuthenticatedUserRequest,
): QueryFilter<IChatSessionDocument> => {
  if (!req.query.startDate && !req.query.endDate) {
    return {};
  }
  const createdAt: { $gte?: Date; $lte?: Date } = {};
  if (req.query.startDate) {
    const startDate = new Date(req.query.startDate as string);
    if (isNaN(startDate.getTime())) {
      throw new BadRequestError('Invalid start date format');
    }
    createdAt.$gte = startDate;
  }
  if (req.query.endDate) {
    const endDate = new Date(req.query.endDate as string);
    if (isNaN(endDate.getTime())) {
      throw new BadRequestError('Invalid end date format');
    }
    createdAt.$lte = endDate;
  }
  return { createdAt };
};

const sharedFlagConstraint = (
  req: AuthenticatedUserRequest,
): QueryFilter<IChatSessionDocument> => {
  if (req.query.shared === undefined) {
    return {};
  }
  const sharedValue = validateBooleanParam(
    req.query.shared as string,
    'shared parameter',
  );
  return sharedValue === undefined ? {} : { isShared: sharedValue };
};

export interface ListSearch {
  escaped: string;
  contentMatchIds?: mongoose.Types.ObjectId[];
}

export const listSearchClause = ({
  escaped,
  contentMatchIds,
}: ListSearch): QueryFilter<IChatSessionDocument> => ({
  $or: [
    { title: { $regex: escaped, $options: 'i' } },
    ...(contentMatchIds && contentMatchIds.length > 0
      ? [{ _id: { $in: contentMatchIds } }]
      : []),
  ],
});

/** The request's `projectId`, date range and `shared` query params as one clause, apart from access, state and search. */
export const listQueryConstraints = (
  req: AuthenticatedUserRequest,
): QueryFilter<IChatSessionDocument> => {
  const constraints: QueryFilter<IChatSessionDocument> = {};
  applyProjectIdQueryFilter(constraints, req);
  return {
    ...constraints,
    ...dateRangeConstraint(req),
    ...sharedFlagConstraint(req),
  };
};

/**
 * Access, state and search stay separate `$and` members so none can overwrite another's `$or`.
 * An access `$or` (`{ $and: [{ ...scope, $or }, ...rest] }`) becomes the top-level `$or`, with every other
 * member repeated in each branch: Mongo then reads each branch in index order and merges them, where an
 * `$or` under an `$and` is read in full and sorted in memory.
 */
export const composeListFilter = (
  listFilter: QueryFilter<IChatSessionDocument>,
  ...parts: Array<QueryFilter<IChatSessionDocument> | undefined>
): QueryFilter<IChatSessionDocument> => {
  const members = [
    listFilter,
    ...parts.filter(
      (part): part is QueryFilter<IChatSessionDocument> =>
        part !== undefined && Object.keys(part).length > 0,
    ),
  ];
  const [access, ...rest] =
    Object.keys(listFilter).length === 1 ? (listFilter.$and ?? []) : [];
  if (!Array.isArray(access?.$or)) {
    return { $and: members };
  }
  const { $or: branches, ...scope } = access;
  const others = [...rest, ...members.slice(1)];
  return {
    $or: branches.map((branch) => ({ $and: [scope, branch, ...others] })),
  };
};

/**
 * The access, state, query-param and search filters of one list request as a
 * single `$and`. A search resolves content matches within the sessions the
 * access and state filters accept, so the content cap never drops accessible
 * results.
 */
export const buildListQuery = async (
  req: AuthenticatedUserRequest,
  o: {
    orgId: string;
    listFilter: QueryFilter<IChatSessionDocument>;
    stateFilter: QueryFilter<IChatSessionDocument>;
    formatSpecifiers?: boolean;
  },
): Promise<{
  filter: QueryFilter<IChatSessionDocument>;
  queryConstraints: QueryFilter<IChatSessionDocument>;
}> => {
  const queryConstraints = listQueryConstraints(req);
  let search: ListSearch | undefined;
  if (req.query.search) {
    const escaped = validateAndEscapeSearch(req.query.search, {
      formatSpecifiers: o.formatSpecifiers,
    });
    search = {
      escaped,
      contentMatchIds: await findSessionIdsMatchingContent(o.orgId, escaped, {
        ownerId: String(req.user?.userId ?? ''),
        sessionFilter: composeListFilter(
          o.listFilter,
          o.stateFilter,
          queryConstraints,
        ),
      }),
    };
  }
  return {
    filter: composeListFilter(
      o.listFilter,
      o.stateFilter,
      queryConstraints,
      search && listSearchClause(search),
    ),
    queryConstraints,
  };
};

export const buildFilter = (
  req: AuthenticatedUserRequest,
  orgId: string,
  userId: string,
  id?: string, // conversationId or searchId
  owned: boolean = true,
  shared: boolean = true,
  contentMatchIds?: mongoose.Types.ObjectId[],
  accessibleProjectIds?: mongoose.Types.ObjectId[],
): Record<string, unknown> => {
  if (!owned && !shared) {
    throw new BadRequestError('Either owned or shared must be true');
  }
  const filter: Record<string, unknown> = {
    orgId: new mongoose.Types.ObjectId(orgId),
    isDeleted: false,
    isArchived: false,
    $or: [
      ...(owned ? [{ userId: new mongoose.Types.ObjectId(userId) }] : []),
      ...(shared
        ? [
            {
              $and: [
                { isShared: true },
                {
                  'sharedWith.userId': new mongoose.Types.ObjectId(userId),
                },
              ],
            },
          ]
        : []),
      // Third access branch: a chat explicitly shared to its project
      // ('projectVisibility: project') is visible to every member with at
      // least viewer access to that project — see ProjectService.
      ...(shared && accessibleProjectIds && accessibleProjectIds.length > 0
        ? [
            {
              projectId: { $in: accessibleProjectIds },
              projectVisibility: 'project',
            },
          ]
        : []),
    ],
  };

  if (id) {
    filter._id = new mongoose.Types.ObjectId(id);
  }

  applyProjectIdQueryFilter(filter, req);

  if (req.query.search) {
    filter.$and = [
      listSearchClause({
        escaped: validateAndEscapeSearch(req.query.search),
        contentMatchIds,
      }),
    ];
  }

  Object.assign(filter, dateRangeConstraint(req), sharedFlagConstraint(req));

  return filter;
};

export const buildPaginationMetadata = (
  totalCount: number,
  page: number,
  limit: number,
) => ({
  page,
  limit,
  totalCount,
  totalPages: Math.ceil(totalCount / limit),
  hasNextPage: page * limit < totalCount,
  hasPrevPage: page > 1,
});

/**
 * The `skip`/`limit` window for page `page` of a conversation's messages,
 * where page 1 is the newest `limit` messages and each later page steps
 * further back. The oldest page is short rather than overlapping the page
 * before it, and a page past the start is empty.
 */
export const olderMessagesWindow = (
  totalMessages: number,
  page: number,
  limit: number,
): { skip: number; limit: number } => {
  const end = Math.max(0, totalMessages - (page - 1) * limit);
  const skip = Math.max(0, end - limit);
  return { skip, limit: end - skip };
};

export const buildFiltersMetadata = (
  appliedFilters: any,
  query: any,
  sortOptions?: { field: string; direction: number },
) => {
  const activeFilters = new Set();
  const currentValues: Record<string, any> = {};

  // Helper function to check and add filter
  const addFilterIfApplied = (filterName: string, value: any) => {
    if (value !== undefined && value !== null && value !== '') {
      activeFilters.add(filterName);
      currentValues[filterName] = value;
    }
  };

  // Process common filters
  addFilterIfApplied('search', query.search);
  addFilterIfApplied('shared', query.shared);
  addFilterIfApplied('tags', query.tags);
  addFilterIfApplied('minMessages', query.minMessages);
  addFilterIfApplied('sortBy', query.sortBy);
  addFilterIfApplied('sortOrder', query.sortOrder);
  addFilterIfApplied('startDate', query.startDate);
  addFilterIfApplied('endDate', query.endDate);
  addFilterIfApplied('messageType', query.messageType);

  // Extract and parse query parameters with safe integer validation
  let page: number;
  let limit: number;
  try {
    const pagination = safeParsePagination(
      query.page as string | undefined,
      query.limit as string | undefined,
      1,
      20,
      100,
    );
    page = pagination.page;
    limit = pagination.limit;
  } catch (error: any) {
    throw new BadRequestError(error.message || 'Invalid pagination parameters');
  }

  addFilterIfApplied('page', page);
  addFilterIfApplied('limit', limit);

  // Process date filters
  if (appliedFilters.createdAt) {
    activeFilters.add('dateRange');
    currentValues.dateRange = {
      start: appliedFilters.createdAt.$gte?.toISOString(),
      end: appliedFilters.createdAt.$lte?.toISOString(),
    };
  }

  return {
    applied: {
      filters: Array.from(activeFilters),
      values: currentValues,
    },
    available: {
      shared: {
        values: ['true', 'false'],
        description: 'Filter by shared status',
        current:
          typeof query.shared === 'string'
            ? sanitizeForResponse(query.shared)
            : query.shared || null,
        applied: activeFilters.has('shared'),
      },
      tags: {
        type: 'string',
        description: 'Filter by tags',
        current:
          typeof query.tags === 'string'
            ? sanitizeForResponse(query.tags)
            : query.tags || null,
        applied: activeFilters.has('tags'),
      },
      minMessages: {
        type: 'number',
        description: 'Filter by minimum number of messages',
        current: query.minMessages || null,
        applied: activeFilters.has('minMessages'),
      },
      search: {
        type: 'string',
        description: 'Search in conversation title and messages',
        current:
          typeof query.search === 'string'
            ? sanitizeForResponse(query.search)
            : query.search || null,
        applied: activeFilters.has('search'),
      },
      pagination: {
        page: {
          type: 'number',
          current: page || 1,
          min: 1,
          max: 1000,
          default: 1,
          description: 'Page number for pagination',
          applied: activeFilters.has('pagination'),
        },
        limit: {
          type: 'number',
          current: limit || 20,
          min: 1,
          max: 100,
          default: 20,
          description: 'Number of items per page',
          applied: activeFilters.has('pagination'),
        },
      },
      sorting: {
        sortBy: {
          values: [
            'createdAt',
            'lastActivityAt',
            'title',
            'messageType',
            'content',
          ],
          default: 'lastActivityAt',
          description: 'Field to sort by',
          current:
            typeof query.sortBy === 'string'
              ? sanitizeForResponse(query.sortBy)
              : query.sortBy || 'lastActivityAt',
          applied: activeFilters.has('sorting'),
        },
        sortOrder: {
          values: ['asc', 'desc'],
          default: 'desc',
          description: 'Sort order',
          current:
            typeof query.sortOrder === 'string'
              ? sanitizeForResponse(query.sortOrder)
              : query.sortOrder || 'desc',
          applied: activeFilters.has('sorting'),
        },
      },
      dateFilters: {
        dateRange: {
          type: 'date',
          description: 'Filter by creation date range',
          format: 'ISO 8601 (YYYY-MM-DD)',
          current: {
            start:
              appliedFilters.createdAt?.$gte?.toISOString() ||
              (typeof query.startDate === 'string'
                ? sanitizeForResponse(query.startDate)
                : query.startDate) ||
              null,
            end:
              appliedFilters.createdAt?.$lte?.toISOString() ||
              (typeof query.endDate === 'string'
                ? sanitizeForResponse(query.endDate)
                : query.endDate) ||
              null,
          },
          applied: activeFilters.has('dateRange'),
        },
      },
      messageFilters: {
        messageType: {
          values: ['user_query', 'bot_response', 'error', 'feedback', 'system'],
          description: 'Filter by message type',
          current:
            typeof query.messageType === 'string'
              ? sanitizeForResponse(query.messageType)
              : query.messageType || null,
          applied: activeFilters.has('messageType'),
        },
      },
      sortingMessages: {
        sortBy: {
          values: ['createdAt', 'messageType', 'content'],
          default: 'createdAt',
          description: 'Field to sort messages by',
          current: sortOptions?.field || 'createdAt',
        },
        sortOrder: {
          values: ['asc', 'desc'],
          default: 'desc',
          description: 'Sort order for messages',
          current: sortOptions?.direction === 1 ? 'asc' : 'desc',
        },
      },
    },
  };
};

export const sortMessages = (
  messages: IMessageDocument[],
  sortOptions: { field: keyof IMessage },
) => {
  return [...messages].sort((a, b) => {
    if (sortOptions.field === 'createdAt') {
      return (a.createdAt?.getTime() || 0) - (b.createdAt?.getTime() || 0);
    }
    return String(a[sortOptions.field]) > String(b[sortOptions.field]) ? 1 : -1;
  });
};

export const buildMessageFilter = (req: AuthenticatedUserRequest) => {
  const messageFilter: any = {};
  const { startDate, endDate, messageType } = req.query;

  // Add date range filter if provided
  if (startDate || endDate) {
    messageFilter['messages.createdAt'] = {};
    if (startDate) {
      const parsedStartDate = new Date(startDate as string);
      if (isNaN(parsedStartDate.getTime())) {
        throw new BadRequestError('Invalid start date format');
      }
      messageFilter['messages.createdAt'].$gte = parsedStartDate;
    }
    if (endDate) {
      const parsedEndDate = new Date(endDate as string);
      if (isNaN(parsedEndDate.getTime())) {
        throw new BadRequestError('Invalid end date format');
      }
      messageFilter['messages.createdAt'].$lte = parsedEndDate;
    }
  }

  // Add message type filter if provided
  if (messageType) {
    const validTypes = [
      'user_query',
      'bot_response',
      'error',
      'feedback',
      'system',
      'tool_call',
    ];
    if (!validTypes.includes(messageType as string)) {
      throw new BadRequestError(
        `Invalid message type. Must be one of: ${validTypes.join(', ')}`,
      );
    }
    messageFilter['messages.messageType'] = messageType;
  }

  return messageFilter;
};

export const buildMessageSortOptions = (
  sortBy = 'createdAt',
  sortOrder = 'desc',
) => {
  const allowedSortFields = ['createdAt', 'messageType', 'content'];
  if (!allowedSortFields.includes(sortBy)) {
    throw new BadRequestError(
      `Invalid sort field. Must be one of: ${allowedSortFields.join(', ')}`,
    );
  }

  return {
    field: sortBy,
    direction: sortOrder.toLowerCase() === 'asc' ? 1 : -1,
  };
};

export const buildConversationResponse = (
  // `harnessVersion` is read beside the session row (harness-session-fields).
  conversation: IChatSessionDocument & { harnessVersion?: string },
  userId: string,
  pagination: {
    page: number;
    limit: number;
    skip: number;
    totalMessages: number;
    hasNextPage: boolean;
    hasPrevPage: boolean;
  },
  messages: IMessage[],
  accessView?: AccessView,
) => {
  const { page, limit, skip, totalMessages } = pagination;

  // Calculate proper hasNextPage/hasPrevPage based on total message count
  // hasNextPage means there are older messages (lower indices)
  // hasPrevPage means there are newer messages (higher indices)
  const hasNextPage = skip > 0;
  const hasPrevPage = skip + messages.length < totalMessages;

  return {
    id: conversation._id,
    title: conversationExtensions().views.displayTitle(conversation.title),
    initiator: conversation.initiator,
    createdAt: conversation.createdAt,
    isShared: conversation.isShared,
    // With an access view only the owner sees who else has access, and why the last answer failed.
    sharedWith:
      accessView && !accessView.isOwner ? undefined : conversation.sharedWith,
    status: conversation.status,
    failReason:
      accessView && !accessView.isOwner ? undefined : conversation.failReason,
    projectId: conversation.projectId,
    projectVisibility: conversation.projectVisibility,
    // Runs on the durable harness (LC2FIX): the chat's commands follow the session, not its latest answer.
    durable: conversation.harnessVersion === 'durable-v1',
    messages: viewMessages(messages, userId).map((message) => ({
      ...withoutLabelFields(message),
      citations:
        message.citations?.map((citation) => ({
          citationId: citation.citationId?._id,
          citationData: citation.citationId,
        })) || [],
    })),
    modelInfo: conversation.modelInfo,
    pagination: {
      page,
      limit,
      totalCount: totalMessages,
      totalPages: Math.ceil(totalMessages / limit),
      hasNextPage,
      hasPrevPage,
      messageRange: {
        start: totalMessages - (skip + messages.length) + 1,
        end: totalMessages - skip,
      },
    },
    access: accessView ?? viewerAccess(conversation, userId),
  };
};

type ConversationError = NonNullable<
  IChatSession['conversationErrors']
>[number];

const MODEL_INFO_FIELDS: Array<keyof IAIModel> = [
  'modelKey',
  'modelName',
  'modelProvider',
  'chatMode',
  'modelFriendlyName',
  'reasoningEffort',
];

/** A request body can carry `null` where the type says `string`. */
const isPresent = (value: unknown): boolean =>
  value !== undefined && value !== null;

/** Copies the defined fields of `modelInfo` onto the in-memory conversation. */
export const mirrorModelInfo = (
  conversation: IChatSessionDocument,
  modelInfo: IAIModel,
): void => {
  for (const field of MODEL_INFO_FIELDS) {
    const value = modelInfo[field];
    if (isPresent(value)) {
      assignAiModelField(
        conversation.modelInfo as IAIModel,
        field,
        value as never,
      );
    }
  }
};

/** A turn by someone other than the owner (an edition may allow that) does not change the conversation's stored model or its title. */
export const isOwnersTurn = (
  run: TurnRun | undefined,
  conversation: IChatSessionDocument,
): boolean =>
  String(run?.requestedBy ?? conversation.userId) ===
  String(conversation.userId);

/**
 * Why a turn failed can quote what the turn read, so only the owner's own failures are copied onto the conversation
 * (`failReason`, `conversationErrors`); anyone else's stays on its own error row, whose reader check decides who sees it.
 */
const failsOnConversation = (
  run: TurnRun | undefined,
  conversation: IChatSessionDocument,
): boolean => isOwnersTurn(run, conversation);

/** Who asked a turn: its requester, else the owner of a single-user chat. */
const askerOfTurn = (
  run: TurnRun | undefined,
  conversation: IChatSessionDocument,
): mongoose.Types.ObjectId | undefined =>
  run?.requestedBy ?? conversation.userId;

/**
 * A regeneration that edits the chat's first question moves the title with it, in memory, and whatever saves the chat's
 * state next (the answer, a partial answer, a failure) writes that title with it; restoring the title drops the entry.
 */
export const editedTitles = new WeakMap<object, string>();

const editedTitleOf = (conversation: object): { title?: string } => {
  const title = editedTitles.get(conversation);
  return title === undefined ? {} : { title };
};

export interface SessionPatch {
  status?: string;
  failReason?: string;
  /** With a `Failed` status: who asked the run that failed. */
  failedRunBy?: mongoose.Types.ObjectId;
  /** Clears a failure left by an earlier turn. */
  clearFailReason?: boolean;
  lastActivityAt: number;
  modelInfo?: IAIModel;
  /** Appended to `conversationErrors`; the array is never rewritten from memory. */
  error?: ConversationError;
}

/** How many `conversationErrors` a session keeps, newest last. */
const MAX_CONVERSATION_ERRORS = 50;

/**
 * The terminal write of a turn: only the fields the turn changed, conditional on the session still
 * being live and, with a lease, on this run still holding it. A stale in-memory document never
 * overwrites a newer one. False when nothing matched.
 */
export const patchTurnSession = async (
  conversation: IChatSessionDocument,
  patch: SessionPatch,
  run?: TurnRun,
  dbSession?: ClientSession | null,
): Promise<boolean> => {
  const $set: Record<string, unknown> = {
    ...editedTitleOf(conversation),
    lastActivityAt: patch.lastActivityAt,
  };
  if (patch.status !== undefined) $set.status = patch.status;
  if (patch.failReason !== undefined) $set.failReason = patch.failReason;
  if (patch.failedRunBy !== undefined) $set.failedRunBy = patch.failedRunBy;
  for (const field of MODEL_INFO_FIELDS) {
    const value = patch.modelInfo?.[field];
    if (isPresent(value)) {
      $set[`modelInfo.${field}`] = value;
    }
  }
  const result = await ChatSession.updateOne(
    {
      ...run?.lease?.ownershipFilter(),
      _id: conversation._id,
      isDeleted: false,
    },
    {
      $set,
      ...(patch.clearFailReason && { $unset: { failReason: 1 } }),
      ...(patch.error && {
        $push: {
          conversationErrors: {
            $each: [patch.error],
            $slice: -MAX_CONVERSATION_ERRORS,
          },
        },
      }),
    },
    dbSession ? { session: dbSession } : undefined,
  );
  return result.matchedCount > 0;
};

export interface CompletedTurnOptions {
  session?: ClientSession | null;
  /** Stored on the answer row. */
  modelInfo?: IAIModel;
  /** Also copy `modelInfo` onto the session (the non-streaming routes do; streams set it when the turn starts). */
  assignModelInfo?: boolean;
  run?: TurnRun;
  agent?: boolean;
  /** Which rows the returned conversation carries; unset reads every row back. */
  frame?: StreamFrame;
  /** With `frame: 'turn'`, the rows the turn stored before its answer: its question, then any cards. */
  turnRows?: Array<Record<string, unknown>>;
  /** Called once the answer's write committed, before the response is built. */
  onSaved?: (answer: Record<string, unknown>, citations: ICitation[]) => void;
  answer?: TurnAnswerHooks;
}

/** Inserts a turn's citations on the write's session; a no-op for none. */
const insertCitations = async (
  citations: ICitation[],
  dbSession: ClientSession | null,
): Promise<void> => {
  if (citations.length === 0) return;
  await Citation.insertMany(citations, dbSession ? { session: dbSession } : {});
};

/** Saves the answer a turn produced and closes the session write; the lease is released by the caller afterwards. */
export const saveCompletedTurn = async (
  conversation: IChatSessionDocument,
  completeData: IAIResponse,
  orgId: string,
  options: CompletedTurnOptions = {},
): Promise<Record<string, unknown>> => {
  const { session, modelInfo, run } = options;
  const noun = options.agent ? 'agent conversation' : 'conversation';
  try {
    // Built here, inserted inside the fenced write so a fenced-out run leaves none behind.
    const citations = (
      Array.isArray(completeData.citations) ? completeData.citations : []
    ).map(
      (citation: ICitation) =>
        new Citation({
          content: citation.content,
          chunkIndex: citation.chunkIndex,
          citationType: citation.citationType,
          metadata: {
            ...citation.metadata,
            orgId,
          },
        }),
    );

    const built = stampTurnRow(
      buildAIResponseMessage(
        { data: completeData, statusCode: 200 },
        citations,
        modelInfo,
      ),
      run,
    );
    const aiResponseMessage = options.answer
      ? await options.answer.prepare(built, { conversation, run })
      : built;

    const assignModelInfo =
      options.assignModelInfo === true && isOwnersTurn(run, conversation);
    if (assignModelInfo && modelInfo) {
      mirrorModelInfo(conversation, modelInfo);
    }
    const errorsBefore = conversation.conversationErrors?.length ?? 0;
    const now = Date.now();
    conversation.lastActivityAt = now;
    recordClassifiedFailureOnSession(conversation, completeData, run);
    const patch: SessionPatch = {
      status: conversation.status as string | undefined,
      failReason: conversation.failReason as string | undefined,
      ...(conversation.status === CONVERSATION_STATUS.FAILED && {
        failedRunBy: askerOfTurn(run, conversation),
      }),
      lastActivityAt: now,
      modelInfo: assignModelInfo ? modelInfo : undefined,
      error: conversation.conversationErrors?.[errorsBefore],
    };

    // Insert the answer before the session write — see "Ordering" in the Phase 1 plan: a crash
    // between the two leaves a persisted message under a stale (Inprogress) status, which is recoverable.
    const insertedMessage = await turnWrite(run, session, async (dbSession) => {
      await insertCitations(citations, dbSession);
      const [inserted] = await appendMessages(
        conversation._id,
        conversation.orgId,
        [aiResponseMessage],
        dbSession,
      );
      if (!inserted) {
        throw new InternalServerError(
          `Failed to save the answer of the ${noun}`,
        );
      }
      if (!(await patchTurnSession(conversation, patch, run, dbSession))) {
        throw new InternalServerError(`Failed to update ${noun}`);
      }
      await options.answer?.stored(inserted, { conversation, run, dbSession });
      return inserted;
    });
    const answer = insertedMessage.toObject() as Record<string, unknown>;
    options.onSaved?.(answer, citations);

    return (await attachPopulatedCitations(
      conversation.toObject(),
      options.frame === 'turn'
        ? [...(options.turnRows ?? []), answer]
        : [answer],
      citations,
      session,
      turnViewerId(run, conversation),
      options.frame,
    )) as Record<string, unknown>;
  } catch (error: unknown) {
    logger.error(`Error saving complete ${noun}`, {
      conversationId: conversation._id,
      ...(options.agent && { agentKey: conversation.agentKey }),
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
};

// Helper function to save complete conversation
export const saveCompleteConversation = (
  conversation: IChatSessionDocument,
  completeData: IAIResponse,
  orgId: string,
  session?: ClientSession | null,
  modelInfo?: IAIModel,
  run?: TurnRun,
  answer?: TurnAnswerHooks,
): Promise<Record<string, unknown>> =>
  saveCompletedTurn(conversation, completeData, orgId, {
    session,
    modelInfo,
    assignModelInfo: true,
    run,
    answer,
  });

/** An entry for the session's `conversationErrors`, with the AG-UI run-error metadata filled in. */
export const buildSessionError = (
  errorMessage: string,
  errorType?: string,
  messageId?: string,
  stack?: string,
  metadata?: Map<string, any>,
): NewSessionError => {
  const aguiCode = errorType || 'unknown_error';
  const mergedMetadata = metadata
    ? new Map(metadata)
    : new Map<string, unknown>();
  for (const [key, value] of aguiRunErrorMetadata(aguiCode)) {
    if (!mergedMetadata.has(key)) {
      mergedMetadata.set(key, value);
    }
  }
  // The session document is read on every load, so the stack is not kept on it. The failure is reported once, by
  // the stream's end line or the call line, and never with its text: a stack starts with the error's message.
  if (stack !== undefined) {
    logger.debug('Conversation error', { errorType: aguiCode });
  }
  return {
    message: errorMessage,
    errorType: aguiCode,
    messageId,
    metadata: mergedMetadata,
  };
};

// Helper function to add error to conversation errors array
export const addErrorToConversation = (
  conversation: IChatSessionDocument,
  errorMessage: string,
  errorType?: string,
  messageId?: mongoose.Types.ObjectId,
  stack?: string,
  metadata?: Map<string, any>,
  requestedBy?: mongoose.Types.ObjectId,
): void => {
  if (!conversation.conversationErrors) {
    conversation.conversationErrors = [];
  }
  const aguiCode = errorType || 'unknown_error';
  const mergedMetadata = metadata
    ? new Map(metadata)
    : new Map<string, unknown>();
  for (const [key, value] of aguiRunErrorMetadata(aguiCode)) {
    if (!mergedMetadata.has(key)) {
      mergedMetadata.set(key, value);
    }
  }
  // The session document is read on every load, so the stack is not kept on it. The failure is reported once, by
  // the stream's end line or the call line, and never with its text: a stack starts with the error's message.
  if (stack !== undefined) {
    logger.debug('Conversation error', {
      conversationId: conversation._id,
      errorType: aguiCode,
    });
  }
  conversation.conversationErrors.push({
    message: errorMessage,
    errorType: aguiCode,
    timestamp: new Date(),
    messageId,
    metadata: mergedMetadata,
    ...(requestedBy && { requestedBy }),
  });
};

/**
 * A run is only believed live this long after its last heartbeat, so a crashed
 * Node frees the thread soon. Well above the heartbeat so a few missed beats
 * never hand a live run's thread to a second writer.
 */
const ACTIVE_RUN_WINDOW_MS = 10 * 60 * 1000;
const RUN_HEARTBEAT_MS = 60 * 1000;
/** Covers Stop followed straight by a new message: the old run is still winding down after the cancel. */
const ACTIVE_RUN_SETTLE_MS = 15_000;
const ACTIVE_RUN_POLL_MS = 250;

export interface RunReservation {
  sessionId: mongoose.Types.ObjectId;
  /** Set on the thread while this run owns it; release and heartbeat only act on a match. */
  runId: string;
  /** What the thread was before the run, for `releaseRun` to put back. */
  previousStatus: string;
}

/**
 * Claims the thread for a new run in one atomic update, so two requests that
 * arrive together cannot both start one: a leftover run from before a refresh
 * would otherwise be joined by a second writer of the same session, and the
 * older one could overwrite the newer one's status.
 *
 * Waits briefly when a run is going so a quick Stop-then-send isn't refused
 * while the cancelled run finishes saving, then refuses with a conflict.
 * Returns null when no session matches `filter`, leaving the caller's own
 * lookup to report that. A status older than the run cap counts as idle, so a
 * crashed server can't lock a thread. Reads and writes without a session on
 * purpose: inside a transaction snapshot the status would never change.
 */
export const reserveRun = async (
  filter: QueryFilter<IChatSessionDocument>,
  settleMs: number = ACTIVE_RUN_SETTLE_MS,
): Promise<RunReservation | null> => {
  const deadline = Date.now() + settleMs;
  const runId = randomUUID();
  for (;;) {
    const now = Date.now();
    const previous = await ChatSession.findOneAndUpdate(
      {
        $and: [
          filter,
          {
            $or: [
              { status: { $ne: CONVERSATION_STATUS.INPROGRESS } },
              { lastActivityAt: null },
              { lastActivityAt: { $lte: now - ACTIVE_RUN_WINDOW_MS } },
            ],
          },
        ],
      },
      {
        $set: {
          status: CONVERSATION_STATUS.INPROGRESS,
          lastActivityAt: now,
          activeRunId: runId,
        },
      },
      { returnDocument: 'before' },
    )
      .select('status')
      .lean();
    if (previous) {
      const was = String(previous.status ?? CONVERSATION_STATUS.COMPLETE);
      return {
        sessionId: previous._id,
        runId,
        previousStatus:
          was === CONVERSATION_STATUS.INPROGRESS
            ? CONVERSATION_STATUS.FAILED
            : was,
      };
    }
    const exists = await ChatSession.findOne(filter).select('_id').lean();
    if (!exists) return null;
    if (Date.now() >= deadline) {
      throw new ConflictError(
        'A response is still being generated for this conversation. Wait for it to finish, or stop it, then send your message again.',
      );
    }
    await new Promise((resolve) => setTimeout(resolve, ACTIVE_RUN_POLL_MS));
  }
};

/**
 * Gives the thread back when a run that was reserved never got going, or as a
 * safety net once one has finished. Only touches a session still marked in
 * progress by this very reservation, so neither a status the run itself saved
 * nor a newer request's run is ever overwritten. Best
 * effort: it must not mask the error being handled, and the activity window
 * frees a thread this misses.
 */
export const releaseRun = async (
  reservation: RunReservation | null,
): Promise<void> => {
  if (!reservation) return;
  try {
    await ChatSession.findOneAndUpdate(
      {
        _id: reservation.sessionId,
        status: CONVERSATION_STATUS.INPROGRESS,
        activeRunId: reservation.runId,
      },
      { $set: { status: reservation.previousStatus } },
    ).lean();
  } catch (error: unknown) {
    logger.warn('Failed to release the run reservation', {
      sessionId: String(reservation.sessionId),
      error: error instanceof Error ? error.message : String(error),
    });
  }
};

/**
 * Keeps `lastActivityAt` fresh while a run's AI stream is open, so a long
 * answer, or a run whose browser left, is never mistaken for a crashed one by
 * `reserveRun`. Stops by itself when the stream closes, however it ends. Pass
 * the reservation's `runId` where there is one so a superseded run's beats
 * cannot touch the thread. Does nothing for a run that holds a lease: the
 * lease has its own heartbeat, and a second one would rewrite the session
 * document every minute for nothing.
 */
export const keepRunAlive = (
  stream: { once(event: 'close', listener: () => void): unknown },
  sessionId: mongoose.Types.ObjectId,
  runId?: string,
  lease?: LeaseHandle,
): void => {
  if (lease) return;
  const timer = setInterval(() => {
    void (async () => {
      try {
        await ChatSession.updateOne(
          {
            _id: sessionId,
            status: CONVERSATION_STATUS.INPROGRESS,
            ...(runId ? { activeRunId: runId } : {}),
          },
          { $set: { lastActivityAt: Date.now() } },
          { timestamps: false },
        );
      } catch (error: unknown) {
        logger.warn('Failed to record run activity', {
          sessionId: String(sessionId),
          error: error instanceof Error ? error.message : String(error),
        });
      }
    })();
  }, RUN_HEARTBEAT_MS);
  timer.unref();
  stream.once('close', () => {
    clearInterval(timer);
  });
};

interface FailedTurnOptions {
  session?: ClientSession | null;
  errorType?: string;
  stack?: string;
  metadata?: Map<string, unknown>;
  run?: TurnRun;
  agent?: boolean;
  /** The label the AI backend sent with the error, unchecked. */
  provenance?: unknown;
}

const persistFailedTurn = async (
  conversation: IChatSessionDocument,
  failReason: string,
  options: FailedTurnOptions,
): Promise<void> => {
  const { session, run } = options;
  const noun = options.agent ? 'agent conversation' : 'conversation';
  try {
    const failedMessage = stampTurnRow(
      buildAIFailureResponseMessage(failReason, undefined, undefined, {
        errorCode: adminRefusals().isCode(options.errorType)
          ? options.errorType
          : undefined,
        provenance: options.provenance,
      }),
      run,
    );
    const errorsBefore = conversation.conversationErrors?.length ?? 0;
    const onConversation = failsOnConversation(run, conversation);
    conversation.status = CONVERSATION_STATUS.FAILED;
    conversation.failedRunBy = askerOfTurn(run, conversation);
    const now = Date.now();
    conversation.lastActivityAt = now;
    if (onConversation) {
      conversation.failReason = failReason;
      addErrorToConversation(
        conversation,
        failReason,
        options.errorType,
        undefined,
        options.stack,
        options.metadata,
        askerOfTurn(run, conversation),
      );
    } else if (options.stack !== undefined) {
      logger.debug('Conversation error', {
        conversationId: conversation._id,
        errorType: options.errorType || 'unknown_error',
      });
    }
    const patch: SessionPatch = {
      status: CONVERSATION_STATUS.FAILED,
      ...(onConversation && { failReason }),
      failedRunBy: askerOfTurn(run, conversation),
      lastActivityAt: now,
      error: conversation.conversationErrors?.[errorsBefore],
    };

    // Insert the failure message first — see "Ordering" in the Phase 1 plan.
    const saved = await turnWrite(run, session, async (dbSession) => {
      await appendMessages(
        conversation._id,
        conversation.orgId,
        [failedMessage],
        dbSession,
      );
      return patchTurnSession(conversation, patch, run, dbSession);
    });

    if (!saved) {
      logger.error(`Failed to save ${noun} error state`, {
        conversationId: conversation._id,
      });
    }

    logger.debug(`Marked the ${noun} as failed`, {
      conversationId: conversation._id,
    });
  } catch (error: unknown) {
    // Each family keeps the line its own mark function has always logged.
    logger.error(
      options.agent
        ? 'Failed to mark agent conversation as failed'
        : 'Error marking conversation as failed',
      {
        conversationId: conversation._id,
        error: error instanceof Error ? error.message : String(error),
      },
    );
    throw error;
  }
};

export const markConversationFailed = (
  conversation: IChatSessionDocument,
  failReason: string,
  session?: ClientSession | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, unknown>,
  run?: TurnRun,
  provenance?: unknown,
): Promise<void> =>
  persistFailedTurn(conversation, failReason, {
    session,
    errorType,
    stack,
    metadata,
    run,
    provenance,
  });

/**
 * Persists the partial answer of a run Node gave up on: its browser went away
 * and it outlived the detached-run cap (see `attachUpstreamAbort`), so Python
 * never sent a terminal `RUN_FINISHED`. Must be called from the `onAbandon`
 * path, never `end` (which does not fire once the Readable is destroyed).
 * `replaceMessageId` is set for the regenerate path, which replaces the
 * original message instead of appending a new one.
 */
export const savePartialConversation = async (
  conversation: IChatSessionDocument,
  partialText: string,
  session?: ClientSession | null,
  options?: {
    replaceMessageId?: mongoose.Types.ObjectId | string;
    parts?: IMessagePart[];
    run?: TurnRun;
  },
): Promise<void> => {
  const run = options?.run;
  try {
    const partialMessage = stampTurnRow(
      {
        messageType: 'bot_response',
        content: stripModelAuthoredMarkers(partialText),
        contentFormat: 'MARKDOWN',
        status: 'stopped',
        ...(options?.parts?.length ? { parts: options.parts } : {}),
        createdAt: new Date(),
        updatedAt: new Date(),
      },
      run,
    );
    const now = Date.now();
    conversation.status = CONVERSATION_STATUS.STOPPED;
    conversation.lastActivityAt = now;
    const patch: SessionPatch = {
      status: CONVERSATION_STATUS.STOPPED,
      lastActivityAt: now,
    };

    const saved = await turnWrite(run, session, async (dbSession) => {
      if (options?.replaceMessageId) {
        const updated = await updateMessageById(
          options.replaceMessageId,
          conversation.orgId,
          partialMessage,
          dbSession,
        );
        if (!updated) {
          logger.error('Failed to persist partial answer: message not found', {
            conversationId: conversation._id,
            messageId: options.replaceMessageId,
          });
        }
      } else {
        await appendMessages(
          conversation._id,
          conversation.orgId,
          [partialMessage],
          dbSession,
        );
      }
      return patchTurnSession(conversation, patch, run, dbSession);
    });

    if (!saved) {
      logger.error('Failed to save conversation after partial stop', {
        conversationId: conversation._id,
      });
    }
  } catch (error: unknown) {
    logger.error('Error saving partial conversation', {
      conversationId: conversation._id,
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
};

/**
 * Replace a message (identified by its `_id`) with an error message — used
 * for regeneration. Positional (`messageIndex`) addressing no longer applies
 * once messages live in their own collection.
 */
export const replaceMessageWithError = async (
  conversation: IChatSessionDocument,
  messageId: mongoose.Types.ObjectId | string,
  errorMessage: string,
  session?: ClientSession | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, any>,
  run?: TurnRun,
  provenance?: unknown,
): Promise<void> => {
  try {
    const errorsBefore = conversation.conversationErrors?.length ?? 0;
    const onConversation = failsOnConversation(run, conversation);
    const now = Date.now();
    conversation.status = CONVERSATION_STATUS.FAILED;
    conversation.failedRunBy = askerOfTurn(run, conversation);
    conversation.lastActivityAt = now;

    const messageObjectId =
      typeof messageId === 'string'
        ? new mongoose.Types.ObjectId(messageId)
        : messageId;

    if (onConversation) {
      conversation.failReason = errorMessage;
      addErrorToConversation(
        conversation,
        errorMessage,
        errorType,
        messageObjectId,
        stack,
        metadata,
        askerOfTurn(run, conversation),
      );
    } else if (stack !== undefined) {
      logger.debug('Conversation error', {
        conversationId: conversation._id,
        errorType: errorType || 'unknown_error',
      });
    }

    const patch: SessionPatch = {
      status: CONVERSATION_STATUS.FAILED,
      ...(onConversation && { failReason: errorMessage }),
      failedRunBy: askerOfTurn(run, conversation),
      lastActivityAt: now,
      error: conversation.conversationErrors?.[errorsBefore],
    };
    // Replace the message with an error message, preserving its _id/seq
    const failedMessage = stampTurnRow(
      buildAIFailureResponseMessage(errorMessage, undefined, undefined, {
        provenance,
      }),
      run,
    );
    const saved = await turnWrite(run, session, async (dbSession) => {
      const updatedMessage = await updateMessageById(
        messageId,
        conversation.orgId,
        failedMessage,
        dbSession,
      );
      if (!updatedMessage) {
        logger.error(
          'Failed to replace message with error: message not found',
          { conversationId: conversation._id, messageId },
        );
      }
      return patchTurnSession(conversation, patch, run, dbSession);
    });

    if (!saved) {
      logger.error('Failed to replace message with error', {
        conversationId: conversation._id,
        messageId,
      });
    }

    logger.debug('Message replaced with error', {
      conversationId: conversation._id,
      messageId,
    });
  } catch (error: any) {
    logger.error('Error replacing message with error', {
      conversationId: conversation._id,
      messageId,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Save complete agent conversation data to database
 */
export const saveCompleteAgentConversation = (
  conversation: IChatSessionDocument,
  completeData: IAIResponse,
  orgId: string,
  session?: ClientSession | null,
  modelInfo?: IAIModel,
  run?: TurnRun,
): Promise<Record<string, unknown>> =>
  saveCompletedTurn(conversation, completeData, orgId, {
    session,
    modelInfo,
    assignModelInfo: true,
    run,
    agent: true,
  });

/**
 * Mark agent conversation as failed
 */
export const markAgentConversationFailed = (
  conversation: IChatSessionDocument,
  failReason: string,
  session?: ClientSession | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, unknown>,
  run?: TurnRun,
  provenance?: unknown,
): Promise<void> =>
  persistFailedTurn(conversation, failReason, {
    session,
    errorType,
    stack,
    metadata,
    run,
    provenance,
    agent: true,
  });

/**
 * Build sort options for agent conversations
 */
export const buildAgentConversationSortOptions = (req: any) => {
  const { sortBy = 'lastActivityAt', sortOrder = 'desc' } = req.query;

  const sortOptions: any = {};
  sortOptions[sortBy] = sortOrder === 'asc' ? 1 : -1;

  return sortOptions;
};

/**
 * Delete agent conversation (soft delete)
 */
export const deleteAgentConversation = async (
  conversationId: mongoose.Types.ObjectId,
  agentKey: string,
  userId: string,
  orgId: string,
  /** Runs with the delete, in its unit of work (E7 enqueues erasure here). */
  onDeleted?: (tx: Tx) => Promise<void>,
  // Runs once access is confirmed and before the delete, outside any unit of work; a throw keeps the conversation.
  beforeDelete?: () => Promise<void>,
): Promise<IChatSessionDocument | null> => {
  if (!userId) {
    return null;
  }
  try {
    const live = {
      ...ONLY_AGENT,
      _id: conversationId,
      agentKey,
      orgId,
      isDeleted: false,
    };
    const conversation = await ChatSession.findOne(live);

    if (!conversation) {
      return null;
    }

    await beforeDelete?.();

    if (onDeleted === undefined) {
      conversation.isDeleted = true;
      conversation.deletedBy = userId as any;
      conversation.lastActivityAt = Date.now();
    }
    // With an erasure to enqueue, one conditional write in the unit of work: a retried transaction writes again.
    const remove = async (tx: Tx): Promise<IChatSessionDocument | null> => {
      const saved = await ChatSession.findOneAndUpdate(
        live,
        {
          $set: {
            isDeleted: true,
            deletedBy: userId,
            lastActivityAt: Date.now(),
          },
        },
        { returnDocument: 'after', session: sessionOf(tx) ?? null },
      );
      if (saved && onDeleted !== undefined) {
        // Written with the delete, so a lost enqueue is still found (E7B).
        await markErasureRequested(String(saved._id), orgId, Date.now(), tx);
        await onDeleted(tx);
      }
      return saved;
    };

    const updatedConversation =
      onDeleted !== undefined
        ? await createUnitOfWork(isReplicaSet()).run(remove)
        : await conversation.save();
    if (!updatedConversation) {
      return null;
    }

    logger.debug('Agent conversation deleted', {
      conversationId,
      agentKey,
      userId,
    });

    return updatedConversation;
  } catch (error: any) {
    logger.error('Error deleting agent conversation', {
      conversationId,
      agentKey,
      userId,
      error: error.message,
    });
    // A malformed id can match nothing; any other failure is not "not found".
    if (error instanceof mongoose.Error.CastError) {
      return null;
    }
    throw error;
  }
};

/**
 * Initialize SSE response headers and send connection event
 */
export const initializeSSEResponse = (
  res: Response,
  protocol?: SSEProtocol,
  runId?: string,
): void => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Access-Control-Allow-Origin': '*',
    'X-Accel-Buffering': 'no',
    ...(runId !== undefined && { 'X-Run-Id': runId }),
  });

  res.write(
    isAGUI(protocol)
      ? frameAGUI(AGUIEventType.CUSTOM, {
          name: 'conversation_created',
          value: {
            message: 'SSE connection established',
            ...(runId !== undefined && { runId }),
          },
        })
      : `event: connected\ndata: ${JSON.stringify({ message: 'SSE connection established' })}\n\n`,
  );
  (res as typeof res & { flush?: () => void }).flush?.();
};

/**
 * Send error event to client with optional updated conversation.
 *
 * AG-UI mode: a true stream-level failure this proxy detected itself
 * (never reached Python's own `RUN_FINISHED`/`RUN_ERROR`) — always
 * `RUN_ERROR`, mirroring `AGUIFormatter.error` on the Python side.
 */
export const sendSSEErrorEvent = async (
  res: Response,
  errorMessage: string,
  details?: string,
  conversation?: any,
  protocol?: SSEProtocol,
  /** The code the AI backend refused the request with, which the client words. */
  code?: string,
): Promise<void> => {
  if (isAGUI(protocol)) {
    res.write(
      frameAGUI(AGUIEventType.RUN_ERROR, {
        message: errorMessage,
        code: code ?? (details ? 'streaming_error' : 'unknown_error'),
        ...(conversation ? { conversation } : {}),
      }),
    );
    return;
  }

  // `details` only picks the error code; its raw text never reaches the client.
  const errorData: any = {
    error: errorMessage,
  };

  if (conversation) {
    errorData.conversation = conversation;
  }

  res.write(`event: error\ndata: ${JSON.stringify(errorData)}\n\n`);
};

/** The answer's search was served with less than the full search (the query service says so): the UI shows a note. */
export const searchDegradedMeta = (
  data:
    Pick<IAIResponse, 'searchDegraded' | 'accessNotices'> | null | undefined,
): {
  searchDegraded?: true;
  accessNotices?: NonNullable<IAIResponse['accessNotices']>;
} => ({
  ...(data?.searchDegraded === true ? { searchDegraded: true as const } : {}),
  ...(data?.accessNotices?.length ? { accessNotices: data.accessNotices } : {}),
});

/**
 * Send complete event to client with conversation data — `RUN_FINISHED`
 * in AG-UI mode (mirrors `AGUIFormatter.answer_final`'s `RUN_FINISHED`,
 * which is what this re-emission on top of), legacy `complete` otherwise.
 */

export const sendSSECompleteEvent = (
  res: Response,
  conversation: any,
  recordsUsed: number,
  requestId: string,
  startTime: number,
  protocol?: SSEProtocol,
  extraMeta: Record<string, unknown> = {},
): void => {
  const responsePayload = {
    conversation,
    recordsUsed,
    meta: {
      requestId,
      timestamp: new Date().toISOString(),
      duration: Date.now() - startTime,
      recordsUsed,
      ...extraMeta,
    },
  };

  res.write(
    isAGUI(protocol)
      ? frameAGUI(AGUIEventType.RUN_FINISHED, { result: responsePayload })
      : `event: complete\ndata: ${JSON.stringify(responsePayload)}\n\n`,
  );
};
const errorMessageOf = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

/** The first of `values` that is a non-empty string. */
const firstNonEmpty = (...values: unknown[]): string | undefined =>
  values.find((v): v is string => typeof v === 'string' && v !== '');

interface UpstreamErrorFrame {
  code?: unknown;
  message?: string;
  error?: string;
  stack?: string;
  metadata?: Record<string, unknown>;
}

/** One relay per regeneration's response, so what it keeps of a text message carries from one chunk to the next. */
const regenerationRelays = new WeakMap<Response, StreamRelay>();

const regenerationRelayFor = (res: Response): StreamRelay => {
  let relay = regenerationRelays.get(res);
  if (!relay) {
    relay = new StreamRelay(
      (name) => conversationExtensions().turns.toolCardFor(name) !== undefined,
    );
    regenerationRelays.set(res, relay);
  }
  return relay;
};

/**
 * Regenerate's hooks on the shared AI-stream reader: an error replaces the regenerated message,
 * and a question is handed back to be attached to the answer when it is saved.
 */
export const handleRegenerationStreamData = (
  chunk: Buffer,
  buffer: string,
  existingConversation: IChatSessionDocument | null,
  messageId: mongoose.Types.ObjectId | string | null,
  session: ClientSession | null,
  requestId: string,
  res: Response,
  onCompleteData: (data: IAIResponse) => void,
  protocol?: SSEProtocol,
  accumulator?: StreamedTranscript,
  onUpstreamError?: (code: unknown) => void,
  onAskUserQuestion?: (payload: unknown) => void,
  run?: TurnRun,
  trackWrite?: (write: Promise<void>) => void,
  answer?: TurnAnswerHooks,
): string => {
  const chunkStr = chunk.toString();
  let newBuffer = buffer + chunkStr;

  const events = newBuffer.split('\n\n');
  newBuffer = events.pop() || '';

  let filteredChunk = '';
  const agui = isAGUI(protocol);
  const toolCardFor = (eventName: string): string | undefined =>
    conversationExtensions().turns.toolCardFor(eventName);
  // Only events, names and fields it knows reach the browser; the rest stay here.
  const relay = regenerationRelayFor(res);

  const replaceWithError = (
    errorMessage: string,
    errorType: string,
    stack?: string,
    metadata?: Map<string, unknown>,
    provenance?: unknown,
  ): void => {
    if (!existingConversation || messageId === null || messageId === '') return;
    const write = replaceMessageWithError(
      existingConversation,
      messageId,
      errorMessage,
      session,
      errorType,
      stack,
      metadata,
      run,
      provenance,
    ).catch((error: unknown) => {
      logger.error('Failed to replace message with error', { error });
    });
    trackWrite?.(write);
  };

  const persistCard = (toolName: string, value: unknown): void => {
    if (!existingConversation || !value || typeof value !== 'object') return;
    const card = stampTurnRow(
      {
        messageType: 'tool_call',
        content: '',
        tools: [{ toolName, toolResult: value }],
        createdAt: new Date(),
        updatedAt: new Date(),
      },
      run,
    );
    const write = turnWrite(run, session, async (dbSession) => {
      await appendMessages(
        existingConversation._id,
        existingConversation.orgId,
        [card],
        dbSession,
      );
    }).catch((err: unknown) => {
      logger.error(`Failed to persist ${toolName} tool_call message`, {
        requestId,
        error: errorMessageOf(err),
      });
    });
    trackWrite?.(write);
  };

  for (const event of events) {
    if (event.trim()) {
      const lines = event.split('\n');
      const eventType = lines
        .find((line) => line.startsWith('event:'))
        ?.replace('event:', '')
        .trim();
      const dataLines = lines
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.replace(/^data: ?/, ''));
      const dataLine = dataLines.join('\n');
      // Feeds what a stopped regeneration saves.
      if (agui) accumulator?.feed(eventType, dataLine);

      if (
        agui &&
        (eventType === AGUIEventType.RUN_FINISHED ||
          eventType === AGUIEventType.RUN_ERROR)
      ) {
        // Only the root run's frames end or fail the regeneration (`readRunFrame`).
        const frame = readRunFrame(eventType, dataLine);
        if (frame.kind === 'answer') {
          // Not forwarded: Node sends its own RUN_FINISHED once the answer is saved.
          onCompleteData(frame.result);
        } else if (frame.kind === 'error') {
          // A throw here must not escape the stream's 'data' handler.
          try {
            const errorData = frame.data as UpstreamErrorFrame;
            onUpstreamError?.(errorData?.code);
            replaceWithError(
              firstNonEmpty(errorData.message) ?? CHAT_ERROR_MESSAGES.failed,
              'streaming_error',
              errorData.stack,
              undefined,
              (errorData as { provenance?: unknown }).provenance,
            );
          } catch {
            logBadFrame(eventType, dataLine);
          }
          filteredChunk += relay.relay(eventType, dataLine);
        } else if (frame.kind === 'pass') {
          // A sub-agent's frame, or a root RUN_FINISHED without a `result`: passed on, it decides nothing.
          filteredChunk += relay.relay(eventType, dataLine);
        } else {
          // A frame Node cannot read is not passed on: the regeneration ends on Node's own no-answer path.
          logBadFrame(eventType, dataLine);
        }
      } else if (
        agui &&
        eventType === AGUIEventType.CUSTOM &&
        dataLine &&
        existingConversation
      ) {
        try {
          const eventData = JSON.parse(dataLine);
          if (typeof eventData?.name === 'string') {
            answer?.observe(eventData.name as string, eventData.value);
          }
          if (eventData?.name === 'ask_user_question' && eventData.value) {
            const payload = eventData.value.toolData ?? eventData.value;
            onAskUserQuestion?.(payload);
          }
          const cardTool =
            typeof eventData?.name === 'string'
              ? toolCardFor(eventData.name as string)
              : undefined;
          if (cardTool && eventData.value) {
            persistCard(cardTool, eventData.value);
          }
        } catch {
          logBadFrame('CUSTOM', dataLine);
        }
        filteredChunk += relay.relay(eventType, dataLine);
      } else if (!agui && eventType === 'complete' && dataLine) {
        try {
          const completeData = JSON.parse(dataLine);
          onCompleteData(completeData);
        } catch (parseError: any) {
          logger.error('Failed to parse complete event data', {
            requestId,
            parseError: parseError.message,
            dataLength: dataLine.length,
          });
          filteredChunk += relay.relay(eventType, dataLine);
        }
      } else if (!agui && eventType === 'answer_chunk' && dataLine) {
        // `accumulated` is the running full text, not a delta — see
        // LegacyFormatter.answer_delta.
        try {
          const parsed = JSON.parse(dataLine) as Record<string, unknown>;
          if (typeof parsed.accumulated === 'string') {
            accumulator?.setAccumulatedText(parsed.accumulated);
          }
        } catch {
          // Non-fatal: still forward the frame below.
        }
        filteredChunk += relay.relay(eventType, dataLine);
      } else if (!agui && eventType === 'error' && dataLine) {
        onUpstreamError?.('streaming_error');
        try {
          const errorData = JSON.parse(dataLine) as UpstreamErrorFrame;
          replaceWithError(
            firstNonEmpty(errorData.error, errorData.message) ??
              CHAT_ERROR_MESSAGES.failed,
            'streaming_error',
            errorData.stack,
            errorData.metadata
              ? new Map(Object.entries(errorData.metadata))
              : undefined,
          );
          filteredChunk += relay.relay(eventType, dataLine);
        } catch (parseError: unknown) {
          logger.error('Failed to parse error event data', {
            requestId,
            parseError: errorMessageOf(parseError),
            dataLength: dataLine.length,
          });
          replaceWithError(
            CHAT_ERROR_MESSAGES.failed,
            'parse_error',
            parseError instanceof Error ? parseError.stack : undefined,
          );
          filteredChunk += relay.relay(eventType, dataLine);
        }
      } else if (
        !agui &&
        eventType !== undefined &&
        toolCardFor(eventType) !== undefined &&
        dataLine &&
        existingConversation
      ) {
        try {
          persistCard(toolCardFor(eventType) as string, JSON.parse(dataLine));
        } catch (parseErr: any) {
          logger.warn(
            `Failed to parse ${eventType} event data during regenerate`,
            {
              requestId,
              error: parseErr?.message,
            },
          );
        }
        filteredChunk += relay.relay(eventType, dataLine);
      } else if (
        eventType === 'ask_user_question' &&
        dataLine &&
        existingConversation
      ) {
        try {
          const eventData = JSON.parse(dataLine);
          if (
            eventData &&
            typeof eventData === 'object' &&
            eventData.status === 'success'
          ) {
            const payload = eventData.toolData ?? eventData;
            onAskUserQuestion?.(payload);
          }
        } catch (parseErr: any) {
          logger.warn(
            'Failed to parse ask_user_question event data during regenerate',
            {
              requestId,
              error: parseErr?.message,
            },
          );
        }
        filteredChunk += relay.relay(eventType, dataLine);
      } else {
        filteredChunk += relay.relay(eventType, dataLine);
      }
    }
  }

  if (filteredChunk) {
    res.write(filteredChunk);
    (res as typeof res & { flush?: () => void }).flush?.();
  }

  return newBuffer;
};

/**
 * Handle successful regeneration completion
 */
export const handleRegenerationSuccess = async (
  completeData: IAIResponse,
  existingConversation: IChatSessionDocument,
  messageId: mongoose.Types.ObjectId | string,
  orgId: string,
  session: ClientSession | null,
  modelInfo?: IAIModel,
  askUserQuestionPayload?: unknown,
  staleAskToolCallIds?: mongoose.Types.ObjectId[],
  run?: TurnRun,
  frame?: StreamFrame,
  answer?: TurnAnswerHooks,
): Promise<{
  conversation: any;
  savedCitations: ICitation[];
}> => {
  // Built here, inserted inside the fenced write so a fenced-out run leaves none behind.
  const savedCitations: ICitation[] =
    (completeData.citations as ICitation[] | undefined)?.map(
      (citation: ICitation) =>
        new Citation({
          content: citation.content,
          chunkIndex: citation.chunkIndex ?? 0,
          citationType: citation.citationType,
          metadata: {
            ...citation.metadata,
            orgId,
          },
        }),
    ) ?? [];

  // Build AI response message and replace the original message with it,
  // preserving the original's _id/seq (see updateMessageById).
  const built = stampTurnRow(
    buildAIResponseMessage(
      { statusCode: 200, data: completeData },
      savedCitations,
      modelInfo,
    ),
    run,
  );
  const aiResponseMessage = answer
    ? await answer.prepare(built, { conversation: existingConversation, run })
    : built;
  if (askUserQuestionPayload) {
    attachAskUserQuestionToMessage(aiResponseMessage, askUserQuestionPayload);
  }

  const ownersTurn = isOwnersTurn(run, existingConversation);
  if (modelInfo && ownersTurn) {
    const fieldsToUpdate: Array<keyof IAIModel> = [
      'modelKey',
      'modelName',
      'modelProvider',
      'chatMode',
      'modelFriendlyName',
      'reasoningEffort',
    ];
    for (const field of fieldsToUpdate) {
      const value = modelInfo[field];
      if (value !== undefined && value !== null) {
        assignAiModelField(
          existingConversation.modelInfo as IAIModel,
          field,
          value,
        );
      }
    }
  }

  const errorsBefore = existingConversation.conversationErrors?.length ?? 0;
  const now = Date.now();
  existingConversation.lastActivityAt = now;
  recordClassifiedFailureOnSession(existingConversation, completeData, run);
  const patch: SessionPatch = {
    status: existingConversation.status as string | undefined,
    failReason: existingConversation.failReason as string | undefined,
    ...(existingConversation.status === CONVERSATION_STATUS.FAILED && {
      failedRunBy: askerOfTurn(run, existingConversation),
    }),
    lastActivityAt: now,
    modelInfo: ownersTurn ? modelInfo : undefined,
    error: existingConversation.conversationErrors?.[errorsBefore],
  };

  // The replacement answer carries its own questions payload, so the rows the
  // discarded run left beside it are stale. Non-fatal without a transaction; inside
  // one (fenced, replica set) a failed removal aborts it and the write fails as a whole.
  let removedIds: mongoose.Types.ObjectId[] = [];
  const updatedMessage = await turnWrite(run, session, async (dbSession) => {
    await insertCitations(savedCitations, dbSession);
    const replaced = await updateMessageById(
      messageId,
      existingConversation.orgId,
      aiResponseMessage,
      dbSession,
    );
    if (!replaced) {
      throw new InternalServerError(
        'Failed to update conversation with regenerated response: message not found',
      );
    }
    await answer?.stored(replaced, {
      conversation: existingConversation,
      run,
      dbSession,
    });
    if (
      !(await patchTurnSession(existingConversation, patch, run, dbSession))
    ) {
      throw new InternalServerError(
        'Failed to update conversation with regenerated response',
      );
    }
    if (staleAskToolCallIds !== undefined && staleAskToolCallIds.length > 0) {
      try {
        await retireMessagesById(
          staleAskToolCallIds,
          {
            sessionId: existingConversation._id,
            orgId: existingConversation.orgId,
          },
          dbSession,
          run?.requestedBy,
        );
        removedIds = staleAskToolCallIds;
      } catch (cleanupErr: unknown) {
        logger.warn(
          'Failed to drop stale ask_user_question rows after regenerate',
          {
            conversationId: existingConversation._id,
            messageId,
            error: errorMessageOf(cleanupErr),
          },
        );
      }
    }
    return replaced;
  });

  // Populate citationData across ALL messages so the frontend can rebuild its
  // citationMaps for the entire conversation. Otherwise previous messages lose
  // inline citation chips (see attachPopulatedCitations docstring).
  const responseConversation = await attachPopulatedCitations(
    existingConversation.toObject(),
    [updatedMessage.toObject()],
    savedCitations,
    session,
    turnViewerId(run, existingConversation),
    frame,
  );

  return {
    // A turn-only frame names the cards it retired, since the client keeps the rows it has.
    conversation:
      frame === 'turn'
        ? { ...(responseConversation as Record<string, unknown>), removedIds }
        : responseConversation,
    savedCitations,
  };
};

/**
 * The conversation an error frame carries, read again once the failure is stored: with `frame` `'turn'` only the
 * failed answer's row, otherwise the rows `framedMessages` reads. Undefined when the conversation is gone.
 */
export const reloadForErrorFrame = async (
  conversationId: string,
  messageId: mongoose.Types.ObjectId | string,
  run: TurnRun | undefined,
  frame?: StreamFrame,
  mongoSession?: ClientSession | null,
): Promise<Record<string, unknown> | undefined> => {
  const conversation = await ChatSession.findById(conversationId);
  if (!conversation) return undefined;
  const messages =
    frame === 'turn'
      ? await ChatSessionMessage.find(
          { _id: messageId, sessionId: conversation._id, ...NOT_REMOVED },
          null,
          mongoSession ? { session: mongoSession } : undefined,
        ).lean()
      : await framedMessages(
          conversation._id,
          conversation.orgId,
          frame,
          false,
          mongoSession,
        );
  return {
    ...(attachMessages(
      conversation.toObject(),
      messages,
      turnViewerId(run, conversation),
    ) as Record<string, unknown>),
    ...(frame === 'turn' && { frame }),
  };
};

/**
 * Handle regeneration error and send error event
 */
export const handleRegenerationError = async (
  res: Response,
  error: Error | any,
  existingConversation: IChatSessionDocument | null,
  messageId: mongoose.Types.ObjectId | string | null,
  conversationId: string,
  session: ClientSession | null,
  requestId: string,
  errorType: string = 'regeneration_error',
  protocol?: SSEProtocol,
  run?: TurnRun,
  beforeFrame?: () => Promise<void>,
  frame?: StreamFrame,
  /**
   * Who the error event goes to: the conversation it carries is hidden for them as in a fetch, an agent rule checked as
   * the request's `identity`. Omitted only for a room read.
   */
  reader?: {
    appConfig: AppConfig;
    userId: unknown;
    orgId: unknown;
    identity?: unknown;
    /** The chat its rows are read as (`readSessionOf`). */
    session?: ReaderSession;
  },
): Promise<void> => {
  const errorMessage = userFacingChatError(error);
  const { message: details, stack } = error as {
    message?: string;
    stack?: string;
  };
  let conversationForFrame: unknown;
  let replaced = false;

  if (existingConversation && messageId) {
    try {
      await replaceMessageWithError(
        existingConversation,
        messageId,
        errorMessage,
        session,
        errorType,
        stack,
        undefined,
        run,
      );
      replaced = true;
    } catch (replaceError: unknown) {
      // The caller ends a stopped run differently from a failed one.
      if (replaceError instanceof LeaseLostError) {
        throw replaceError;
      }
      logger.error('Failed to replace message with error', {
        requestId,
        error: errorMessageOf(replaceError),
      });
    }
  }
  // The lease is released before the last frame, so a client that retries on seeing it finds the conversation free.
  await beforeFrame?.();
  if (replaced && existingConversation && messageId !== null) {
    try {
      conversationForFrame = await reloadForErrorFrame(
        conversationId,
        messageId,
        run,
        frame,
        session,
      );
      if (conversationForFrame && reader) {
        // The error row is this run's own, as an answer frame's new answer is: shown to its asker as written.
        conversationForFrame = await conversationForViewer(
          reader.appConfig,
          viewerOf(reader.userId, reader.orgId),
          conversationForFrame,
          {
            freshIds: [messageId],
            ...(reader.identity !== undefined && {
              identity: reader.identity,
            }),
            session: reader.session,
          },
        );
      }
    } catch (reloadError: unknown) {
      logger.error('Failed to reload conversation for error event', {
        requestId,
        error: errorMessageOf(reloadError),
      });
    }
  }
  await sendSSEErrorEvent(
    res,
    errorMessage,
    details,
    conversationForFrame,
    protocol,
    error instanceof AIRequestRefusedError ? error.code : undefined,
  );
};
