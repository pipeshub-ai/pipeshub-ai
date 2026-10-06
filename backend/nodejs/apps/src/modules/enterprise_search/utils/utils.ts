import { displayTitle } from '../services/collaboration/mentions/mention.parser';
import {
  AIServiceResponse,
  IAIModel,
  IAppliedFilterNode,
  IChatAttachmentRef,
  IChatSession,
  IChatSessionDocument,
  IChatSessionMessageDocument,
  IMessage,
  IMessageCitation,
  IMessageDocument,
  IMessagePart,
} from '../types/conversation.interfaces';
import { IAIResponse } from '../types/conversation.interfaces';
import mongoose, { ClientSession, FilterQuery } from 'mongoose';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import Citation, { ICitation } from '../schema/citation.schema';
import { CONVERSATION_STATUS, ONLY_AGENT } from '../constants/constants';
import { Logger } from '../../../libs/services/logger.service';
import { Response } from 'express';
import { ChatSession } from '../schema/chat.session.schema';
import { ChatSessionMessage } from '../schema/chat.session.message.schema';
import { AccessView } from '../services/collaboration/domain/types';
import {
  DRAFT_AGENT_TOOL,
  LIST_AGENT_OPTIONS_TOOL,
  redactAgentDraft,
  redactAgentDrafts,
} from '../services/collaboration/feed/draft-redaction';
import { LeaseLostError } from '../services/collaboration/leases/lease.types';
import {
  stampTurnRow,
  TurnRun,
  turnWrite,
} from '../services/collaboration/turn/turn-run';
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
  SSEProtocol,
} from './agui';
import { StreamedContentAccumulator } from './stream-lifecycle';
import { CHAT_ERROR_MESSAGES, userFacingChatError } from './chat-error-messages';
import { stripSignedUrlLinks } from './signed-url';
import { toWireMentions } from '../services/collaboration/turn/mention-refs';
import { AuthorRefs } from '../services/collaboration/turn/participant-roster';

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

/** Who wrote a `user_query` row and the consent they gave for it; the idempotency and consent fields are flag-on only. */
export interface UserQueryAuthorship {
  authorUserId?: mongoose.Types.ObjectId;
  clientMessageId?: string;
  filesShared?: boolean;
  shareToolResults?: boolean;
  /** The run the row belongs to (PH-07's join key). */
  runId?: string;
  /** Validated mention tokens; omitted when the message mentions nothing. */
  mentions?: IMessage['mentions'];
}

export const buildUserQueryMessage = (
  query: string,
  appliedFilters?: { apps?: IAppliedFilterNode[]; kb?: IAppliedFilterNode[] },
  chatMode?: string,
  attachments?: IChatAttachmentRef[],
  authorship?: UserQueryAuthorship,
): IMessage => ({
  messageType: 'user_query',
  content: query,
  contentFormat: 'MARKDOWN',
  ...(appliedFilters ? { appliedFilters } : {}),
  modelInfo: chatMode ? ({ chatMode } as IAIModel) : undefined,
  ...(attachments && attachments.length > 0 ? { attachments } : {}),
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
 * Case-insensitive substring match against message content, scoped to one
 * org. An unanchored `$regex` can't use an index, so this is a collection
 * scan either way. With `sessionFilter` the scan is limited to the sessions
 * that filter accepts (newest `limit` of them), so other users' matches can
 * never crowd accessible ones out of the capped result.
 */
export const findSessionIdsMatchingContent = async (
  orgId: string,
  escapedSearch: string,
  {
    sessionFilter,
    limit = 10000,
  }: {
    sessionFilter?: FilterQuery<IChatSessionDocument>;
    limit?: number;
  } = {},
): Promise<mongoose.Types.ObjectId[]> => {
  const accessible = sessionFilter
    ? (
        await ChatSession.find(sessionFilter)
          .sort({ lastActivityAt: -1, _id: -1 })
          .limit(limit)
          .select('_id')
          .lean()
          .exec()
      ).map((row) => row._id)
    : undefined;
  if (accessible?.length === 0) {
    return [];
  }
  const rows = await ChatSessionMessage.aggregate<{
    _id: mongoose.Types.ObjectId;
  }>([
    {
      $match: {
        orgId: new mongoose.Types.ObjectId(orgId),
        ...(accessible && { sessionId: { $in: accessible } }),
        content: { $regex: escapedSearch, $options: 'i' },
      },
    },
    { $group: { _id: '$sessionId' } },
    { $limit: limit },
  ]);
  return rows.map((r) => r._id);
};

export const buildAIFailureResponseMessage = (content?: string): IMessage => ({
  messageType: 'error',
  content: content ?? 'Error Generating Response, Please try again',
  contentFormat: 'MARKDOWN',
  createdAt: new Date(),
  updatedAt: new Date(),
});

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
 */
export const allocateSeq = async (
  sessionId: mongoose.Types.ObjectId | string,
  n: number,
  mongoSession?: ClientSession | null,
): Promise<number> => {
  const updated = await ChatSession.findOneAndUpdate(
    { _id: sessionId },
    { $inc: { nextSeq: n, rev: 1 } },
    {
      new: true,
      projection: { nextSeq: 1 }, // overrides the schema's `select: false` for this one read
      session: mongoSession || undefined,
    },
  );
  if (!updated) {
    throw new NotFoundError(
      'Chat session not found while allocating message sequence',
    );
  }
  return updated.nextSeq as number;
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
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    out[key] = stripSignedUrlsDeep(item, depth + 1, budget);
  }
  return out;
};

/**
 * The single choke point for persisted assistant data: `appendMessages` and
 * `updateMessageById` are the only writers of message rows, and both pass
 * through here, so no signed URL is stored in any field (content, reasoning,
 * tool results, parts, reference data, citations, ...) whichever path
 * produced it. User queries are stored as typed.
 */
export const sanitizeMessageForPersistence = (message: IMessage): IMessage =>
  message.messageType === 'user_query' || message.messageType === 'note'
    ? message
    : (stripSignedUrlsDeep(message, 0, { nodes: 0 }) as IMessage);

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
  const endSeq = await allocateSeq(sessionId, messages.length, mongoSession);
  const startSeq = endSeq - messages.length + 1;
  const toInsert = messages.map((message, i) => ({
    ...sanitizeMessageForPersistence(message),
    sessionId,
    orgId,
    seq: startSeq + i,
  }));
  return ChatSessionMessage.insertMany(toInsert, {
    ordered: true,
    session: mongoSession || undefined,
  }) as unknown as Promise<IChatSessionMessageDocument[]>;
};

/**
 * Wholesale-replace one message's content, preserving its `_id`/`sessionId`/
 * `orgId`/`seq`. Mirrors the old `conversation.messages[index] = newMessage`
 * array-element replacement (regeneration intentionally discards the prior
 * message's citations/feedback/etc. — only its identity and position stay
 * stable), so this is a full `findOneAndReplace`, not a `$set` merge (which
 * would leave stale fields the new content doesn't mention).
 */
export const updateMessageById = async (
  messageId: mongoose.Types.ObjectId | string,
  newContent: IMessage,
  mongoSession?: ClientSession | null,
): Promise<IChatSessionMessageDocument | null> => {
  const existing = await ChatSessionMessage.findById(messageId, undefined, {
    session: mongoSession || undefined,
  });
  if (!existing) {
    return null;
  }
  return ChatSessionMessage.findOneAndReplace(
    { _id: messageId },
    {
      ...sanitizeMessageForPersistence(newContent),
      sessionId: existing.sessionId,
      orgId: existing.orgId,
      seq: existing.seq,
    },
    { new: true, session: mongoSession || undefined, runValidators: true },
  );
};

const hasAskUserQuestionTool = (msg: IMessage | undefined): boolean =>
  Boolean(msg?.tools?.some((tool) => tool.toolName?.includes('ask_user_question')));

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
  messages: Array<IMessage & { _id?: mongoose.Types.ObjectId }>,
  botMessageId: mongoose.Types.ObjectId | string,
): mongoose.Types.ObjectId[] => {
  const botIndex = messages.findIndex(
    (msg) => msg._id?.toString() === botMessageId.toString(),
  );
  if (botIndex < 0) {
    return [];
  }
  const stale: mongoose.Types.ObjectId[] = [];
  const collect = (from: number, step: number): void => {
    for (let i = from; i >= 0 && i < messages.length; i += step) {
      const row = messages[i];
      if (row?.messageType !== 'tool_call') break;
      if (row._id && hasAskUserQuestionTool(row)) stale.push(row._id);
    }
  };
  collect(botIndex - 1, -1);
  collect(botIndex + 1, 1);
  return stale;
};

export const deleteMessagesById = async (
  messageIds: mongoose.Types.ObjectId[],
  mongoSession?: ClientSession | null,
  scope?: { sessionId: mongoose.Types.ObjectId; orgId: mongoose.Types.ObjectId },
): Promise<number> => {
  if (messageIds.length === 0) {
    return 0;
  }
  const result = await ChatSessionMessage.deleteMany(
    { _id: { $in: messageIds }, ...scope },
    mongoSession ? { session: mongoSession } : undefined,
  );
  return result.deletedCount ?? 0;
};

/** Append a feedback entry to one message's `feedback` array. */
export const appendMessageFeedback = async (
  messageId: mongoose.Types.ObjectId | string,
  feedbackEntry: unknown,
  mongoSession?: ClientSession | null,
): Promise<IChatSessionMessageDocument | null> => {
  return ChatSessionMessage.findOneAndUpdate(
    { _id: messageId },
    { $push: { feedback: feedbackEntry } },
    { new: true, session: mongoSession || undefined, runValidators: true },
  );
};

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
    skip?: number;
    limit?: number;
    populateCitations?: boolean;
    sort?: 1 | -1;
  } = {},
  mongoSession?: ClientSession | null,
): Promise<any[]> => {
  const { skip = 0, limit, populateCitations = false, sort = 1 } = options;
  if (limit !== undefined && limit <= 0) {
    return [];
  }
  let query = ChatSessionMessage.find({ sessionId }).sort({ seq: sort });
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

/**
 * `viewerId` is who the response goes to: another participant's agent draft is replaced by a
 * placeholder. Left out, every draft is redacted.
 */
export const attachMessages = (
  session: any,
  messages: any[],
  viewerId?: string,
): any => {
  const { nextSeq, sessionType, ...cleanSession } = session ?? {};
  return {
    ...withoutErrorStacks(cleanSession as object),
    messages: (messages || []).map((message: any) => {
      const { sessionId, orgId, seq, ...rest } = message;
      return redactAgentDraft(rest, viewerId);
    }),
  };
};

/** Who a turn's response frames are for: the run's requester, else the owner of a single-user chat. */
export const turnViewerId = (
  run: TurnRun | undefined,
  conversation: { userId?: { toString(): string } | null } | null | undefined,
): string | undefined =>
  run?.requestedBy?.toString() ?? conversation?.userId?.toString();

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
 */
export const attachPopulatedCitations = async (
  session: any,
  fallbackMessages: any[],
  fallbackCitations: ICitation[],
  mongoSession?: ClientSession | null,
  viewerId?: string,
): Promise<any> => {
  const sessionId = session?._id;
  let messages = fallbackMessages;

  // Only attempt the populate round-trip when Mongoose is actually connected.
  // In unit tests (and any environment without an active DB connection) the
  // default Mongoose buffering would hang this call for ~10s before failing,
  // which is both slow and unnecessary — the fallback branch handles those
  // cases using the caller-supplied messages.
  const isConnected = mongoose.connection?.readyState === 1;

  if (sessionId && isConnected) {
    try {
      messages = await getMessages(
        sessionId,
        { populateCitations: true },
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
            const doc = populated as ICitation & {
              _id?: mongoose.Types.ObjectId;
            };
            return {
              ...citation,
              citationId: doc._id,
              citationData: doc as ICitation,
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

export const recordClassifiedFailureOnSession = (
  conversation: IChatSessionDocument,
  completeData: IAIResponse,
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
  );
};

export const buildAIResponseMessage = (
  aiResponse: AIServiceResponse<IAIResponse>,
  citations: ICitation[] = [],
  modelInfo?: IAIModel,
): IMessage => {
  const data = aiResponse?.data;
  // A `stopped` run may have been cancelled before any tokens streamed —
  // an empty answer is valid there (see AnswerFinalizer's cancelled branch).
  // `waiting_input` is the ask_user_question pause: the card is the turn,
  // not a prose answer.
  const allowsEmptyAnswer =
    data?.status === 'stopped' || data?.status === 'waiting_input';
  if (!data || (!data.answer && !allowsEmptyAnswer)) {
    throw new InternalServerError('AI response must include an answer');
  }

  const message: IMessage = {
    messageType: isClassifiedFailureAnswer(data)
      ? 'error'
      : 'bot_response',
    createdAt: new Date(),
    updatedAt: new Date(),
    content: data.answer ?? '',
    contentFormat: 'MARKDOWN',
    citations: citations.map((citation) => ({
      citationId: citation._id as mongoose.Types.ObjectId,
    })),
    confidence: data.confidence,
    ...(data.answerMatchType === 'Capability Card' ? { answerMatchType: 'Capability Card' as const } : {}),
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
    modelInfo: modelInfo,
  };

  // Include referenceData if present (IDs for follow-up queries)
  // This stores technical IDs that were in the response for later reference
  // Filter out invalid items (must have name and at least key or id)
  if (
    data.referenceData &&
    Array.isArray(data.referenceData)
  ) {
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
  if (
    data.parts &&
    Array.isArray(data.parts) &&
    data.parts.length > 0
  ) {
    message.parts = data.parts;
  }

  if (data.status === 'stopped') {
    message.status = 'stopped';
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
    ...existingTools.filter((tool) => !tool.toolName?.includes('ask_user_question')),
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
const toolResultsFromParts = (
  parts?: IMessagePart[],
): Array<{
  tool_id?: string;
  tool_name?: string;
  args?: Record<string, unknown>;
  result: string;
  result_summary?: string;
  status: 'success' | 'error';
  artifact_id?: string;
}> => {
  if (!parts || parts.length === 0) {
    return [];
  }
  return parts
    .filter((part) => part.type === 'tool_call' && part.toolName)
    .map((part) => {
      // Later turns may be asked by someone else: the model gets the fact of the draft, not its contents.
      if (part.toolName?.endsWith(DRAFT_AGENT_TOOL)) {
        return {
          tool_id: part.toolCallId,
          tool_name: part.toolName,
          result: 'An agent draft card was shown to the user who asked.',
          status: 'success' as const,
        };
      }
      if (part.toolName?.endsWith(LIST_AGENT_OPTIONS_TOOL)) {
        return {
          tool_id: part.toolCallId,
          tool_name: part.toolName,
          result: "The asker's agent options were listed for them.",
          status: 'success' as const,
        };
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
        status: part.status === 'failed' ? ('error' as const) : ('success' as const),
        ...(part.artifactId && { artifact_id: part.artifactId }),
      };
    });
};

type PreviousToolResult = ReturnType<typeof toolResultsFromParts>[number];

const askToolResults = (
  msg: IMessage | undefined,
): PreviousToolResult[] => {
  if (!msg?.tools?.length) {
    return [];
  }
  return msg.tools
    .filter((tool) => tool.toolName.includes('ask_user_question') && tool.toolResult)
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

const withTurnAskToolResults = (
  messages: IMessage[],
  botIndex: number,
  existing: PreviousToolResult[],
): PreviousToolResult[] => {
  if (existing.some((row) => row.tool_name?.includes('ask_user_question'))) {
    return existing;
  }
  const extra = askResultsForTurn(messages, botIndex);
  return extra.length ? [...existing, ...extra] : existing;
};

export const formatPreviousConversations = (
  messages: IMessage[],
  participants?: AuthorRefs,
  /** Guest-agent key -> its `agent:<handle>` ref, for the answers it wrote. */
  guestRefs?: ReadonlyMap<string, string>,
) => {
  const result: Array<Record<string, unknown>> = [];
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (
      !msg ||
      msg.messageType === 'error' ||
      msg.messageType === 'tool_call'
    ) {
      continue;
    }
    // A note is information for the AI backend's shared-chat rendering; with no roster there is no one to attribute it to.
    if (msg.messageType === 'note' && !participants) {
      continue;
    }
    let toolResults: PreviousToolResult[] =
      msg.messageType === 'bot_response' ? toolResultsFromParts(msg.parts) : [];
    if (msg.messageType === 'bot_response') {
      // The questions row is a sibling of the answer, not part of its `parts`;
      // a resume needs it on this turn.
      toolResults = withTurnAskToolResults(messages, i, toolResults);
    }
    const author = msg.authorUserId as { toString(): string } | undefined;
    const authorRef =
      participants &&
      (msg.messageType === 'user_query' || msg.messageType === 'note')
        ? participants.refs.get(author?.toString() ?? participants.ownerId)
        : undefined;
    const mentions =
      participants && msg.messageType === 'note'
        ? toWireMentions(msg.mentions, participants.refs)
        : [];
    const agentRef =
      msg.messageType === 'bot_response' && msg.respondingAgentKey
        ? guestRefs?.get(msg.respondingAgentKey)
        : undefined;
    result.push({
      content: msg.content,
      role: msg.messageType,
      ...(authorRef !== undefined && { authorRef }),
      ...(agentRef !== undefined && { agentRef }),
      ...(mentions.length > 0 && { mentions }),
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
    });
  }
  return result;
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

export const buildSortOptions = (req: AuthenticatedUserRequest) => {
  const allowedSortFields = ['createdAt', 'lastActivityAt', 'title'];
  const sortField = allowedSortFields.includes(req.query?.sortBy as string)
    ? (req.query?.sortBy as string)
    : 'lastActivityAt';

  return {
    [sortField]: req.query.sortOrder === 'asc' ? 1 : -1,
    _id: -1, // Secondary sort for consistency
  };
};

type SharedWithRow = { userId?: { toString(): string } | null; accessLevel?: string };

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

const recipientAccessLevel = (
  sharedWith: SharedWithRow[] | undefined,
  userId: string,
): string =>
  userSharedWithRows(sharedWith).find((row) =>
    sharedWithUserIdEquals(row, userId),
  )?.accessLevel || 'read';

/** With `access` (flag on) the role comes from the policy, so a write recipient or team member is not reported as `read`. */
export const addComputedFields = <
  T extends {
    initiator: { toString(): string };
    sharedWith?: SharedWithRow[];
  },
>(
  conversation: T,
  userId: string,
  access?: AccessView,
) => {
  const cleaned = withoutErrorStacks(conversation);
  return {
    ...cleaned,
    ...('title' in cleaned && { title: displayTitle(cleaned.title as string | undefined) }),
    isOwner: conversation.initiator.toString() === userId,
    accessLevel:
      access?.accessLevel ??
      recipientAccessLevel(conversation.sharedWith, userId),
    ...(access && { access }),
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
/** Sentinel accepted by `?projectId=` to mean "sessions with no project link". Mirrors projects/types/project.interfaces.ts::PROJECT_ID_UNASSIGNED. */
export const PROJECT_ID_UNASSIGNED_QUERY_VALUE = 'unassigned';

/**
 * AND-composes an optional `?projectId=<id>|unassigned` query filter onto an
 * existing chatSessions filter object, mutating it in place. Shared by
 * `buildFilter` so the list/detail endpoints support the same query
 * contract. A malformed (non-ObjectId, non-'unassigned') value is ignored
 * rather than thrown, since it only narrows a list — never called on a
 * `require`d id param.
 */
export const applyProjectIdQueryFilter = (
  filter: FilterQuery<IChatSessionDocument>,
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
): FilterQuery<IChatSessionDocument> => {
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
): FilterQuery<IChatSessionDocument> => {
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
}: ListSearch): FilterQuery<IChatSessionDocument> => ({
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
): FilterQuery<IChatSessionDocument> => {
  const constraints: FilterQuery<IChatSessionDocument> = {};
  applyProjectIdQueryFilter(constraints, req);
  return {
    ...constraints,
    ...dateRangeConstraint(req),
    ...sharedFlagConstraint(req),
  };
};

/** Access, state and search stay separate `$and` members so none can overwrite another's `$or` (F-17). */
export const composeListFilter = (
  listFilter: FilterQuery<IChatSessionDocument>,
  ...parts: Array<FilterQuery<IChatSessionDocument> | undefined>
): FilterQuery<IChatSessionDocument> => ({
  $and: [
    listFilter,
    ...parts.filter(
      (part): part is FilterQuery<IChatSessionDocument> =>
        part !== undefined && Object.keys(part).length > 0,
    ),
  ],
});

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
    listFilter: FilterQuery<IChatSessionDocument>;
    stateFilter: FilterQuery<IChatSessionDocument>;
    formatSpecifiers?: boolean;
  },
): Promise<{
  filter: FilterQuery<IChatSessionDocument>;
  queryConstraints: FilterQuery<IChatSessionDocument>;
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
) => {
  if (!owned && !shared) {
    throw new BadRequestError('Either owned or shared must be true');
  }
  const filter: any = {
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
  conversation: IChatSessionDocument,
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
    title: displayTitle(conversation.title),
    initiator: conversation.initiator,
    createdAt: conversation.createdAt,
    isShared: conversation.isShared,
    // With the flag on (access view present) only the owner sees who else has access (I-9).
    sharedWith:
      accessView && !accessView.isOwner ? undefined : conversation.sharedWith,
    status: conversation.status,
    failReason: conversation.failReason,
    projectId: conversation.projectId,
    projectVisibility: conversation.projectVisibility,
    messages: redactAgentDrafts(messages, userId).map((message) => ({
      ...message,
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
    access: accessView ?? {
      isOwner: conversation.initiator.toString() === userId,
      accessLevel: recipientAccessLevel(conversation.sharedWith, userId),
    },
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

export interface SessionPatch {
  status?: string;
  failReason?: string;
  /** Clears a failure left by an earlier turn. */
  clearFailReason?: boolean;
  lastActivityAt: number;
  modelInfo?: IAIModel;
  /** Appended to `conversationErrors`; the array is never rewritten from memory. */
  error?: ConversationError;
  /** For a write that changes a row in place: appending a row already bumps `rev`. */
  bumpRev?: boolean;
}

/**
 * The terminal write of a turn: only the fields the turn changed, conditional on the session still
 * being live and, with a lease, on this run still holding it. A stale in-memory document never
 * overwrites a newer one (CL-01). False when nothing matched.
 */
export const patchTurnSession = async (
  conversation: IChatSessionDocument,
  patch: SessionPatch,
  run?: TurnRun,
  dbSession?: ClientSession | null,
): Promise<boolean> => {
  const $set: Record<string, unknown> = {
    lastActivityAt: patch.lastActivityAt,
  };
  if (patch.status !== undefined) $set.status = patch.status;
  if (patch.failReason !== undefined) $set.failReason = patch.failReason;
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
      ...(patch.error && { $push: { conversationErrors: patch.error } }),
      ...(patch.bumpRev && { $inc: { rev: 1 } }),
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

    // Create AI response message
    const aiResponseMessage = stampTurnRow(
      buildAIResponseMessage(
        { data: completeData, statusCode: 200 },
        citations,
        modelInfo,
      ),
      run,
    );

    if (options.assignModelInfo && modelInfo) {
      mirrorModelInfo(conversation, modelInfo);
    }
    const errorsBefore = conversation.conversationErrors?.length ?? 0;
    const now = Date.now();
    conversation.lastActivityAt = now;
    recordClassifiedFailureOnSession(conversation, completeData);
    const patch: SessionPatch = {
      status: conversation.status as string | undefined,
      failReason: conversation.failReason as string | undefined,
      lastActivityAt: now,
      modelInfo: options.assignModelInfo ? modelInfo : undefined,
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
      return inserted;
    });

    return (await attachPopulatedCitations(
      conversation.toObject(),
      [insertedMessage.toObject()],
      citations,
      session,
      turnViewerId(run, conversation),
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
): Promise<Record<string, unknown>> =>
  saveCompletedTurn(conversation, completeData, orgId, {
    session,
    modelInfo,
    assignModelInfo: true,
    run,
  });

// Helper function to add error to conversation errors array
export const addErrorToConversation = (
  conversation: IChatSessionDocument,
  errorMessage: string,
  errorType?: string,
  messageId?: mongoose.Types.ObjectId,
  stack?: string,
  metadata?: Map<string, any>,
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
  conversation.conversationErrors.push({
    message: errorMessage,
    errorType: aguiCode,
    timestamp: new Date(),
    messageId,
    stack,
    metadata: mergedMetadata,
  });
};

interface FailedTurnOptions {
  session?: ClientSession | null;
  errorType?: string;
  stack?: string;
  metadata?: Map<string, unknown>;
  run?: TurnRun;
  agent?: boolean;
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
      buildAIFailureResponseMessage(failReason),
      run,
    );
    const errorsBefore = conversation.conversationErrors?.length ?? 0;
    conversation.status = CONVERSATION_STATUS.FAILED;
    conversation.failReason = failReason;
    const now = Date.now();
    conversation.lastActivityAt = now;
    addErrorToConversation(
      conversation,
      failReason,
      options.errorType,
      undefined,
      options.stack,
      options.metadata,
    );
    const patch: SessionPatch = {
      status: CONVERSATION_STATUS.FAILED,
      failReason,
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
        failReason,
      });
    }

    logger.debug(`Marked the ${noun} as failed`, {
      conversationId: conversation._id,
      failReason,
    });
  } catch (error: unknown) {
    logger.error(`Error marking ${noun} as failed`, {
      conversationId: conversation._id,
      error: error instanceof Error ? error.message : String(error),
    });
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
): Promise<void> =>
  persistFailedTurn(conversation, failReason, {
    session,
    errorType,
    stack,
    metadata,
    run,
  });

/**
 * Persists whatever the user had already seen when the connection dropped
 * before Python could send a terminal `RUN_FINISHED` — the passive-disconnect
 * counterpart to `saveCompleteConversation`/`saveCompleteAgentConversation`.
 * Must be called from the stream's `close`/`onDisconnect` path, never `end`
 * (which does not fire once `attachUpstreamAbort` has destroyed the
 * Readable). `replaceMessageId` is set for the regenerate path, which
 * replaces the original message instead of appending a new one.
 */
export const savePartialConversation = async (
  conversation: IChatSessionDocument,
  partialText: string,
  session?: ClientSession | null,
  options?: {
    replaceMessageId?: mongoose.Types.ObjectId | string;
    run?: TurnRun;
  },
): Promise<void> => {
  const run = options?.run;
  try {
    const partialMessage = stampTurnRow(
      {
        messageType: 'bot_response',
        content: partialText,
        contentFormat: 'MARKDOWN',
        status: 'stopped',
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
      bumpRev: Boolean(options?.replaceMessageId),
    };

    const saved = await turnWrite(run, session, async (dbSession) => {
      if (options?.replaceMessageId) {
        const updated = await updateMessageById(
          options.replaceMessageId,
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
): Promise<void> => {
  try {
    const errorsBefore = conversation.conversationErrors?.length ?? 0;
    const now = Date.now();
    conversation.status = CONVERSATION_STATUS.FAILED;
    conversation.failReason = errorMessage;
    conversation.lastActivityAt = now;

    const messageObjectId =
      typeof messageId === 'string'
        ? new mongoose.Types.ObjectId(messageId)
        : messageId;

    // Add error to errors array
    addErrorToConversation(
      conversation,
      errorMessage,
      errorType,
      messageObjectId,
      stack,
      metadata,
    );

    const patch: SessionPatch = {
      status: CONVERSATION_STATUS.FAILED,
      failReason: errorMessage,
      lastActivityAt: now,
      error: conversation.conversationErrors?.[errorsBefore],
      bumpRev: true,
    };
    // Replace the message with an error message, preserving its _id/seq
    const failedMessage = stampTurnRow(
      buildAIFailureResponseMessage(errorMessage),
      run,
    );
    const saved = await turnWrite(run, session, async (dbSession) => {
      const updatedMessage = await updateMessageById(
        messageId,
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
        errorMessage,
      });
    }

    logger.debug('Message replaced with error', {
      conversationId: conversation._id,
      messageId,
      errorMessage,
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
): Promise<void> =>
  persistFailedTurn(conversation, failReason, {
    session,
    errorType,
    stack,
    metadata,
    run,
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
): Promise<IChatSessionDocument | null> => {
  if (!userId) {
    return null;
  }
  try {
    const conversation = await ChatSession.findOne({
      ...ONLY_AGENT,
      _id: conversationId,
      agentKey,
      orgId,
      isDeleted: false,
    });

    if (!conversation) {
      return null;
    }

    conversation.isDeleted = true;
    conversation.deletedBy = userId as any;
    conversation.lastActivityAt = Date.now();

    const updatedConversation = await conversation.save();

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
    'Content-Type': 'text/event-stream',
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
  (res as any).flush?.();
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
): Promise<void> => {
  if (isAGUI(protocol)) {
    res.write(
      frameAGUI(AGUIEventType.RUN_ERROR, {
        message: errorMessage,
        code: details ? 'streaming_error' : 'unknown_error',
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
): void => {
  const responsePayload = {
    conversation,
    recordsUsed,
    meta: {
      requestId,
      timestamp: new Date().toISOString(),
      duration: Date.now() - startTime,
      recordsUsed,
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
  message?: string;
  error?: string;
  stack?: string;
  metadata?: Record<string, unknown>;
}

/**
 * Handle regeneration stream data events
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
  accumulator?: StreamedContentAccumulator,
  onUpstreamError?: () => void,
  onAskUserQuestion?: (payload: unknown) => void,
  run?: TurnRun,
  trackWrite?: (write: Promise<void>) => void,
): string => {
  const chunkStr = chunk.toString();
  let newBuffer = buffer + chunkStr;

  const events = newBuffer.split('\n\n');
  newBuffer = events.pop() || '';

  let filteredChunk = '';
  const agui = isAGUI(protocol);

  const replaceWithError = (
    errorMessage: string,
    errorType: string,
    stack?: string,
    metadata?: Map<string, unknown>,
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
    ).catch((err: unknown) => {
      logger.error('Failed to replace message with error', {
        requestId,
        error: errorMessageOf(err),
      });
    });
    trackWrite?.(write);
  };

  const persistDraft = (draft: unknown): void => {
    if (!existingConversation || !draft || typeof draft !== 'object') return;
    const card = stampTurnRow(
      {
        messageType: 'tool_call',
        content: '',
        tools: [{ toolName: DRAFT_AGENT_TOOL, toolResult: draft }],
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
      logger.error('Failed to persist draft_agent tool_call message', {
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

      if (agui && eventType === AGUIEventType.RUN_FINISHED && dataLine) {
        // Mirrors the legacy `complete` branch below — `result` on
        // Python's RUN_FINISHED IS the same completion_data shape
        // `complete.data` carries today (see `AGUIFormatter.answer_final`).
        try {
          const parsed = JSON.parse(dataLine);
          onCompleteData(parsed.result ?? parsed);
        } catch (parseError: any) {
          logger.error('Failed to parse RUN_FINISHED event data', {
            requestId,
            parseError: parseError.message,
            dataLine,
          });
          filteredChunk += event + '\n\n';
        }
      } else if (agui && eventType === AGUIEventType.TEXT_MESSAGE_CONTENT && dataLine) {
        // Feed the passive-disconnect accumulator so a partial answer
        // survives a dropped connection — see savePartialConversation.
        try {
          accumulator?.feedTextMessageContent(JSON.parse(dataLine));
        } catch {
          // Non-fatal: still forward the frame below.
        }
        filteredChunk += event + '\n\n';
      } else if (agui && eventType === AGUIEventType.RUN_ERROR && dataLine) {
        try {
          const errorData = JSON.parse(dataLine) as UpstreamErrorFrame;
          onUpstreamError?.();
          replaceWithError(
            firstNonEmpty(errorData.message) ?? CHAT_ERROR_MESSAGES.failed,
            'streaming_error',
            errorData.stack,
          );
          filteredChunk += event + '\n\n';
        } catch (parseError: any) {
          logger.error('Failed to parse RUN_ERROR event data', {
            requestId,
            parseError: parseError.message,
            dataLine,
          });
          filteredChunk += event + '\n\n';
        }
      } else if (
        agui &&
        eventType === AGUIEventType.CUSTOM &&
        dataLine &&
        existingConversation
      ) {
        try {
          const eventData = JSON.parse(dataLine);
          if (eventData?.name === 'ask_user_question' && eventData.value) {
            const payload = eventData.value.toolData ?? eventData.value;
            onAskUserQuestion?.(payload);
          }
          if (eventData?.name === 'agent_draft' && eventData.value) {
            persistDraft(eventData.value);
          }
        } catch (parseErr: any) {
          logger.warn('Failed to parse CUSTOM event data during regenerate', {
            requestId,
            error: parseErr?.message,
          });
        }
        filteredChunk += event + '\n\n';
      } else if (!agui && eventType === 'complete' && dataLine) {
        try {
          const completeData = JSON.parse(dataLine);
          onCompleteData(completeData);
        } catch (parseError: any) {
          logger.error('Failed to parse complete event data', {
            requestId,
            parseError: parseError.message,
            dataLine,
          });
          filteredChunk += event + '\n\n';
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
        filteredChunk += event + '\n\n';
      } else if (!agui && eventType === 'error' && dataLine) {
        onUpstreamError?.();
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
          filteredChunk += event + '\n\n';
        } catch (parseError: unknown) {
          logger.error('Failed to parse error event data', {
            requestId,
            parseError: errorMessageOf(parseError),
            dataLine,
          });
          replaceWithError(
            CHAT_ERROR_MESSAGES.failed,
            'parse_error',
            parseError instanceof Error ? parseError.stack : undefined,
          );
          filteredChunk += event + '\n\n';
        }
      } else if (
        !agui &&
        eventType === 'agent_draft' &&
        dataLine &&
        existingConversation
      ) {
        try {
          persistDraft(JSON.parse(dataLine));
        } catch (parseErr: any) {
          logger.warn('Failed to parse agent_draft event data during regenerate', {
            requestId,
            error: parseErr?.message,
          });
        }
        filteredChunk += event + '\n\n';
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
        filteredChunk += event + '\n\n';
      } else {
        filteredChunk += event + '\n\n';
      }
    }
  }

  if (filteredChunk) {
    res.write(filteredChunk);
    (res as any).flush?.();
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
): Promise<{
  conversation: any;
  savedCitations: ICitation[];
}> => {
  // Built here, inserted inside the fenced write so a fenced-out run leaves none behind.
  const savedCitations: ICitation[] =
    completeData.citations?.map(
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
    ) || [];

  // Build AI response message and replace the original message with it,
  // preserving the original's _id/seq (see updateMessageById).
  const aiResponseMessage = stampTurnRow(
    buildAIResponseMessage(
      { statusCode: 200, data: completeData },
      savedCitations,
      modelInfo,
    ),
    run,
  );
  if (askUserQuestionPayload) {
    attachAskUserQuestionToMessage(aiResponseMessage, askUserQuestionPayload);
  }

  if (modelInfo) {
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
        assignAiModelField(existingConversation.modelInfo as IAIModel, field, value);
      }
    }
  }

  const errorsBefore = existingConversation.conversationErrors?.length ?? 0;
  const now = Date.now();
  existingConversation.lastActivityAt = now;
  recordClassifiedFailureOnSession(existingConversation, completeData);
  const patch: SessionPatch = {
    status: existingConversation.status as string | undefined,
    failReason: existingConversation.failReason as string | undefined,
    lastActivityAt: now,
    modelInfo,
    error: existingConversation.conversationErrors?.[errorsBefore],
    bumpRev: true,
  };

  // The replacement answer carries its own questions payload, so the rows the
  // discarded run left beside it are stale. Non-fatal without a transaction; inside
  // one (fenced, replica set) a failed delete aborts it and the write fails as a whole.
  const updatedMessage = await turnWrite(run, session, async (dbSession) => {
    await insertCitations(savedCitations, dbSession);
    const replaced = await updateMessageById(
      messageId,
      aiResponseMessage,
      dbSession,
    );
    if (!replaced) {
      throw new InternalServerError(
        'Failed to update conversation with regenerated response: message not found',
      );
    }
    if (
      !(await patchTurnSession(existingConversation, patch, run, dbSession))
    ) {
      throw new InternalServerError(
        'Failed to update conversation with regenerated response',
      );
    }
    if (staleAskToolCallIds !== undefined && staleAskToolCallIds.length > 0) {
      try {
        await deleteMessagesById(staleAskToolCallIds, dbSession, {
          sessionId: existingConversation._id,
          orgId: existingConversation.orgId,
        });
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
  );

  return {
    conversation: responseConversation,
    savedCitations,
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
  if (replaced && existingConversation) {
    try {
      // Reload conversation to get updated state
      const updatedConversation = await ChatSession.findById(conversationId);
      if (updatedConversation) {
        const messages = await getMessages(
          updatedConversation._id as mongoose.Types.ObjectId,
          {},
          session,
        );
        conversationForFrame = attachMessages(
          updatedConversation.toObject(),
          messages,
          turnViewerId(run, updatedConversation),
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
  );
};

/**
 * Monotonic stage timings for one streaming chat request, emitted as a single
 * log line. Pairs with the Python `StageTimer` so the Node and Python halves of
 * a request can be read side by side.
 */
export class StageTimer {
  private readonly t0 = process.hrtime.bigint();
  private last = this.t0;
  private readonly marks: Array<[string, number]> = [];
  private emitted = false;

  mark(stage: string): void {
    const now = process.hrtime.bigint();
    this.marks.push([stage, Number(now - this.last) / 1e6]);
    this.last = now;
  }

  get totalMs(): number {
    return Number(process.hrtime.bigint() - this.t0) / 1e6;
  }

  /** Safe to call more than once; only the first call logs. */
  emit(label: string, extra: Record<string, unknown> = {}): void {
    if (this.emitted) return;
    this.emitted = true;
    const stages = this.marks.map(([n, ms]) => `${n}=${ms.toFixed(0)}ms`).join(' ');
    logger.info(`⏱ ${label} total=${this.totalMs.toFixed(0)}ms | ${stages}`, extra);
  }
}
