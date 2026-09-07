import {
  AIServiceResponse,
  IAIModel,
  IAppliedFilterNode,
  IChatAttachmentRef,
  IChatSessionDocument,
  IChatSessionMessageDocument,
  IMessage,
  IMessageCitation,
  IMessageDocument,
  IMessagePart,
} from '../types/conversation.interfaces';
import { IAIResponse } from '../types/conversation.interfaces';
import mongoose, { ClientSession } from 'mongoose';
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
import { safeParsePagination } from '../../../utils/safe-integer';
import {
  sanitizeForResponse,
  validateBooleanParam,
  validateNoXSS,
  validateNoFormatSpecifiers,
} from '../../../utils/xss-sanitization';
import { AGUIEventType, frameAGUI, isAGUI, SSEProtocol } from './agui';

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

export const buildUserQueryMessage = (
  query: string,
  appliedFilters?: { apps?: IAppliedFilterNode[]; kb?: IAppliedFilterNode[] },
  chatMode?: string,
  attachments?: IChatAttachmentRef[],
): IMessage => ({
  messageType: 'user_query',
  content: query,
  contentFormat: 'MARKDOWN',
  ...(appliedFilters ? { appliedFilters } : {}),
  modelInfo: chatMode ? ({ chatMode } as IAIModel) : undefined,
  ...(attachments && attachments.length > 0 ? { attachments } : {}),
  createdAt: new Date(),
  updatedAt: new Date(),
});

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
 * used by both `buildFilter` and `buildAgentConversationFilter` (and by their
 * callers' async content-match lookup — see `findSessionIdsMatchingContent`)
 * so the two computations of "the escaped search term" can never drift.
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
 * scan either way; `limit` bounds the result set (and therefore memory) at
 * the cost of silently truncating pathological searches — see the Phase 1
 * plan's "Search" section. Replacing this with a real text index is a
 * Phase 2 follow-up.
 */
export const findSessionIdsMatchingContent = async (
  orgId: string,
  escapedSearch: string,
  limit = 10000,
): Promise<mongoose.Types.ObjectId[]> => {
  const rows = await ChatSessionMessage.aggregate<{
    _id: mongoose.Types.ObjectId;
  }>([
    {
      $match: {
        orgId: new mongoose.Types.ObjectId(orgId),
        content: { $regex: escapedSearch, $options: 'i' },
      },
    },
    { $group: { _id: '$sessionId' } },
    { $limit: limit },
  ]);
  return rows.map((r) => r._id);
};

export const buildAIFailureResponseMessage = (): IMessage => ({
  messageType: 'error',
  content: 'Error Generating Response, Please try again',
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
    { $inc: { nextSeq: n } },
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
    ...message,
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
      ...newContent,
      sessionId: existing.sessionId,
      orgId: existing.orgId,
      seq: existing.seq,
    },
    { new: true, session: mongoSession || undefined, runValidators: true },
  );
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
 * response shape.
 */
export const attachMessages = (session: any, messages: any[]): any => {
  const { nextSeq, sessionType, ...cleanSession } = session ?? {};
  return {
    ...cleanSession,
    messages: (messages || []).map((message: any) => {
      const { sessionId, orgId, seq, ...rest } = message;
      return rest;
    }),
  };
};

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

  const attached = attachMessages(session, messages);
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

export const buildAIResponseMessage = (
  aiResponse: AIServiceResponse<IAIResponse>,
  citations: ICitation[] = [],
  modelInfo?: IAIModel,
): IMessage => {
  if (!aiResponse?.data?.answer) {
    throw new InternalServerError('AI response must include an answer');
  }

  const message: IMessage = {
    messageType: 'bot_response',
    createdAt: new Date(),
    updatedAt: new Date(),
    content: aiResponse.data.answer,
    contentFormat: 'MARKDOWN',
    citations: citations.map((citation) => ({
      citationId: citation._id as mongoose.Types.ObjectId,
    })),
    confidence: aiResponse.data.confidence,
    followUpQuestions:
      aiResponse.data.followUpQuestions?.map((q) => ({
        question: q.question,
        confidence: q.confidence,
        reasoning: q.reasoning,
      })) || [],
    metadata: {
      processingTimeMs: aiResponse.data.metadata?.processingTimeMs,
      modelVersion: aiResponse.data.metadata?.modelVersion,
      aiTransactionId: aiResponse.data.metadata?.aiTransactionId,
      reason: aiResponse.data?.reason,
    },
    modelInfo: modelInfo,
  };

  // Include referenceData if present (IDs for follow-up queries)
  // This stores technical IDs that were in the response for later reference
  // Filter out invalid items (must have name and at least key or id)
  if (
    aiResponse.data.referenceData &&
    Array.isArray(aiResponse.data.referenceData)
  ) {
    message.referenceData = aiResponse.data.referenceData.filter((item) => {
      // Ensure item has name and at least one of key or id (id can be optional)
      return item?.name;
    });
  }

  // Present only when PIPESHUB_PERSIST_REASONING=true on the Python side
  // (see reasoning_persistence.py) — absent for every existing client/run.
  if (
    aiResponse.data.reasoning &&
    Array.isArray(aiResponse.data.reasoning) &&
    aiResponse.data.reasoning.length > 0
  ) {
    message.reasoning = aiResponse.data.reasoning;
  }

  // Ordered agent-activity transcript (`agui` protocol only — see
  // TranscriptCollector/respond.py) — absent for the legacy protocol and
  // for every pre-existing conversation. Copied through as-is: Python has
  // already bounded every field (tool args/result previews, truncated
  // reasoning) before this reaches Node, so no full external tool result
  // ever lands in Mongo via this path.
  if (
    aiResponse.data.parts &&
    Array.isArray(aiResponse.data.parts) &&
    aiResponse.data.parts.length > 0
  ) {
    message.parts = aiResponse.data.parts;
  }

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

export const formatPreviousConversations = (messages: IMessage[]) => {
  return messages
    .filter(
      (msg) => msg.messageType !== 'error' && msg.messageType !== 'tool_call',
    )
    .map((msg) => {
      const toolResults =
        msg.messageType === 'bot_response'
          ? toolResultsFromParts(msg.parts)
          : [];
      return {
        content: msg.content,
        role: msg.messageType,
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
      };
    });
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

export const addComputedFields = <
  T extends {
    initiator: { toString(): string };
    sharedWith?: Array<{ userId: { toString(): string }; accessLevel: string }>;
  },
>(
  conversation: T,
  userId: string,
) => {
  return {
    ...conversation,
    isOwner: conversation.initiator.toString() === userId,
    accessLevel:
      conversation.sharedWith?.find(
        (share) => share.userId.toString() === userId,
      )?.accessLevel || 'read',
  };
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
export const buildFilter = (
  req: AuthenticatedUserRequest,
  orgId: string,
  userId: string,
  id?: string, // conversationId or searchId
  owned: boolean = true,
  shared: boolean = true,
  contentMatchIds?: mongoose.Types.ObjectId[],
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
    ],
  };

  if (id) {
    filter._id = new mongoose.Types.ObjectId(id);
  }

  // Handle search with XSS validation
  if (req.query.search) {
    const escapedSearch = validateAndEscapeSearch(req.query.search);

    filter.$and = [
      {
        $or: [
          { title: { $regex: escapedSearch, $options: 'i' } },
          ...(contentMatchIds && contentMatchIds.length > 0
            ? [{ _id: { $in: contentMatchIds } }]
            : []),
        ],
      },
    ];
  }

  // Handle date range
  if (req.query.startDate || req.query.endDate) {
    filter.createdAt = {};
    if (req.query.startDate) {
      const startDate = new Date(req.query.startDate as string);
      if (isNaN(startDate.getTime())) {
        throw new BadRequestError('Invalid start date format');
      }
      filter.createdAt.$gte = startDate;
    }
    if (req.query.endDate) {
      const endDate = new Date(req.query.endDate as string);
      if (isNaN(endDate.getTime())) {
        throw new BadRequestError('Invalid end date format');
      }
      filter.createdAt.$lte = endDate;
    }
  }

  // Handle shared/private filter with XSS validation
  if (req.query.shared !== undefined) {
    const sharedValue = validateBooleanParam(
      req.query.shared as string,
      'shared parameter',
    );
    if (sharedValue !== undefined) {
      filter.isShared = sharedValue;
    }
  }

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
) => {
  const { page, limit, skip, totalMessages } = pagination;

  // Calculate proper hasNextPage/hasPrevPage based on total message count
  // hasNextPage means there are older messages (lower indices)
  // hasPrevPage means there are newer messages (higher indices)
  const hasNextPage = skip > 0;
  const hasPrevPage = skip + messages.length < totalMessages;

  return {
    id: conversation._id,
    title: conversation.title,
    initiator: conversation.initiator,
    createdAt: conversation.createdAt,
    isShared: conversation.isShared,
    sharedWith: conversation.sharedWith,
    status: conversation.status,
    failReason: conversation.failReason,
    messages: messages.map((message) => ({
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
    access: {
      isOwner: conversation.initiator.toString() === userId,
      accessLevel:
        conversation.sharedWith?.find(
          (share) => share.userId.toString() === userId,
        )?.accessLevel || 'read',
    },
  };
};

// Helper function to save complete conversation
export const saveCompleteConversation = async (
  conversation: IChatSessionDocument,
  completeData: IAIResponse,
  orgId: string,
  session?: ClientSession | null,
  modelInfo?: IAIModel,
): Promise<any> => {
  try {
    // Save citations first
    const citations = await Promise.all(
      completeData.citations?.map(async (citation: any) => {
        const newCitation = new Citation({
          content: citation.content,
          chunkIndex: citation.chunkIndex,
          citationType: citation.citationType,
          metadata: {
            ...citation.metadata,
            orgId,
          },
        });
        return session ? newCitation.save({ session }) : newCitation.save();
      }) || [],
    );

    // Create AI response message
    const aiResponseMessage = buildAIResponseMessage(
      { data: completeData, statusCode: 200 },
      citations,
      modelInfo,
    );

    // Insert it before flipping the session to Complete — see "Ordering" in
    // the Phase 1 plan: a crash between the two leaves a persisted message
    // under a stale (Inprogress) status, which is recoverable.
    const [insertedMessage] = await appendMessages(
      conversation._id as mongoose.Types.ObjectId,
      conversation.orgId,
      [aiResponseMessage],
      session,
    );

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
          assignAiModelField(conversation.modelInfo as IAIModel, field, value);
        }
      }
    }
    conversation.lastActivityAt = Date.now();
    conversation.status = CONVERSATION_STATUS.COMPLETE;

    // Save updated conversation
    const updatedConversation = session
      ? await conversation.save({ session })
      : await conversation.save();

    if (!updatedConversation) {
      throw new InternalServerError('Failed to update conversation');
    }

    return attachPopulatedCitations(
      updatedConversation.toObject(),
      [insertedMessage!.toObject()],
      citations,
      session,
    );
  } catch (error: any) {
    logger.error('Error saving complete conversation', {
      conversationId: conversation._id,
      error: error.message,
    });
    throw error;
  }
};

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
  conversation.conversationErrors.push({
    message: errorMessage,
    errorType: errorType || 'unknown',
    timestamp: new Date(),
    messageId,
    stack,
    metadata,
  });
};

export const markConversationFailed = async (
  conversation: IChatSessionDocument,
  failReason: string,
  session?: ClientSession | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, any>,
): Promise<void> => {
  try {
    // Insert the failure message first — see "Ordering" in the Phase 1 plan.
    const failedMessage = buildAIFailureResponseMessage();
    failedMessage.content = failReason;
    await appendMessages(
      conversation._id as mongoose.Types.ObjectId,
      conversation.orgId,
      [failedMessage],
      session,
    );

    conversation.status = CONVERSATION_STATUS.FAILED;
    conversation.failReason = failReason;
    conversation.lastActivityAt = Date.now();

    // Add error to errors array
    addErrorToConversation(
      conversation,
      failReason,
      errorType,
      undefined,
      stack,
      metadata,
    );

    // Save failed conversation
    const savedWithError = session
      ? await conversation.save({ session })
      : await conversation.save();

    if (!savedWithError) {
      logger.error('Failed to save conversation error state', {
        conversationId: conversation._id,
        failReason,
      });
    }

    logger.debug('Conversation marked as failed', {
      conversationId: conversation._id,
      failReason,
    });
  } catch (error: any) {
    logger.error('Error marking conversation as failed', {
      conversationId: conversation._id,
      error: error.message,
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
): Promise<void> => {
  try {
    conversation.status = CONVERSATION_STATUS.FAILED;
    conversation.failReason = errorMessage;
    conversation.lastActivityAt = Date.now();

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

    // Replace the message with an error message, preserving its _id/seq
    const failedMessage = buildAIFailureResponseMessage();
    failedMessage.content = errorMessage;
    const updatedMessage = await updateMessageById(
      messageId,
      failedMessage,
      session,
    );
    if (!updatedMessage) {
      logger.error('Failed to replace message with error: message not found', {
        conversationId: conversation._id,
        messageId,
      });
    }

    // Save updated conversation
    const savedWithError = session
      ? await conversation.save({ session })
      : await conversation.save();

    if (!savedWithError) {
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
export const saveCompleteAgentConversation = async (
  conversation: IChatSessionDocument,
  completeData: IAIResponse,
  orgId: string,
  session?: ClientSession | null,
  modelInfo?: IAIModel,
): Promise<any> => {
  try {
    // Save citations first
    const citations = await Promise.all(
      completeData.citations?.map(async (citation: any) => {
        const newCitation = new Citation({
          content: citation.content,
          chunkIndex: citation.chunkIndex,
          citationType: citation.citationType,
          metadata: {
            ...citation.metadata,
            orgId,
          },
        });
        return session ? newCitation.save({ session }) : newCitation.save();
      }) || [],
    );

    // Create AI response message
    const aiResponseMessage = buildAIResponseMessage(
      { data: completeData, statusCode: 200 },
      citations,
      modelInfo,
    );

    const [insertedMessage] = await appendMessages(
      conversation._id as mongoose.Types.ObjectId,
      conversation.orgId,
      [aiResponseMessage],
      session,
    );

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
          assignAiModelField(conversation.modelInfo as IAIModel, field, value);
        }
      }
    }
    conversation.lastActivityAt = Date.now();
    conversation.status = CONVERSATION_STATUS.COMPLETE;

    // Save updated conversation
    const updatedConversation = session
      ? await conversation.save({ session })
      : await conversation.save();

    if (!updatedConversation) {
      throw new InternalServerError('Failed to update agent conversation');
    }

    return attachPopulatedCitations(
      updatedConversation.toObject(),
      [insertedMessage!.toObject()],
      citations,
      session,
    );
  } catch (error: any) {
    logger.error('Error saving complete agent conversation', {
      conversationId: conversation._id,
      agentKey: conversation.agentKey,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Mark agent conversation as failed
 */
export const markAgentConversationFailed = async (
  conversation: IChatSessionDocument,
  failReason: string,
  session?: ClientSession | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, any>,
): Promise<void> => {
  try {
    const failedMessage = buildAIFailureResponseMessage();
    await appendMessages(
      conversation._id as mongoose.Types.ObjectId,
      conversation.orgId,
      [failedMessage],
      session,
    );

    conversation.status = CONVERSATION_STATUS.FAILED;
    conversation.failReason = failReason;
    conversation.lastActivityAt = Date.now();

    addErrorToConversation(
      conversation,
      failReason,
      errorType,
      undefined,
      stack,
      metadata,
    );

    // Save failed conversation
    const savedWithError = session
      ? await conversation.save({ session })
      : await conversation.save();

    if (!savedWithError) {
      logger.error('Failed to save agent conversation error state', {
        conversationId: conversation._id,
        agentKey: conversation.agentKey,
        failReason,
      });
    }

    logger.debug('Agent conversation marked as failed', {
      conversationId: conversation._id,
      agentKey: conversation.agentKey,
      failReason,
    });
  } catch (error: any) {
    logger.error('Failed to mark agent conversation as failed', {
      conversationId: conversation._id,
      agentKey: conversation.agentKey,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Build filter for agent conversations. `ONLY_AGENT` is applied here (in
 * addition to always requiring a concrete `agentKey`) as defense-in-depth
 * now that agent and plain-chat sessions share one collection.
 */
export const buildAgentConversationFilter = (
  req: any,
  orgId: string,
  userId: string,
  agentKey: string,
  conversationId?: string,
  contentMatchIds?: mongoose.Types.ObjectId[],
) => {
  const filter: any = {
    ...ONLY_AGENT,
    agentKey,
    orgId: new mongoose.Types.ObjectId(orgId),
    $or: [{ userId: new mongoose.Types.ObjectId(userId) }],
    isDeleted: false,
  };

  if (conversationId) {
    filter._id = new mongoose.Types.ObjectId(conversationId);
  }

  // Handle search with XSS and format string validation
  if (req.query.search) {
    const escapedSearch = validateAndEscapeSearch(req.query.search, {
      formatSpecifiers: true,
    });

    filter.$and = [
      {
        $or: [
          { title: { $regex: escapedSearch, $options: 'i' } },
          ...(contentMatchIds && contentMatchIds.length > 0
            ? [{ _id: { $in: contentMatchIds } }]
            : []),
        ],
      },
    ];
  }

  // Handle date range
  if (req.query.startDate || req.query.endDate) {
    filter.createdAt = {};
    if (req.query.startDate) {
      const startDate = new Date(req.query.startDate as string);
      if (isNaN(startDate.getTime())) {
        throw new BadRequestError('Invalid start date format');
      }
      filter.createdAt.$gte = startDate;
    }
    if (req.query.endDate) {
      const endDate = new Date(req.query.endDate as string);
      if (isNaN(endDate.getTime())) {
        throw new BadRequestError('Invalid end date format');
      }
      filter.createdAt.$lte = endDate;
    }
  }

  // Handle shared/private filter with XSS validation
  if (req.query.shared !== undefined) {
    const sharedValue = validateBooleanParam(
      req.query.shared as string,
      'shared parameter',
    );
    if (sharedValue !== undefined) {
      filter.isShared = sharedValue;
    }
  }

  return filter;
};

/**
 * Build shared with me filter for agent conversations
 */
export const buildAgentSharedWithMeFilter = (
  req: any,
  orgId: string,
  userId: string,
  agentKey: string,
) => {
  const filter: any = {
    ...ONLY_AGENT,
    agentKey,
    orgId: new mongoose.Types.ObjectId(orgId),
    isDeleted: false,
    isShared: true,
    'sharedWith.userId': userId,
  };

  // Add additional filters
  if (req.query.status) {
    filter.status = req.query.status;
  }

  if (req.query.isArchived) {
    filter.isArchived = req.query.isArchived === 'true';
  }

  return filter;
};

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
 * Validate agent conversation access
 */
export const validateAgentConversationAccess = async (
  conversationId: string,
  agentKey: string,
  userId: string,
  orgId: string,
  accessLevel: 'read' | 'write' = 'read',
): Promise<IChatSessionDocument | null> => {
  try {
    const conversation = await ChatSession.findOne({
      ...ONLY_AGENT,
      _id: conversationId,
      agentKey,
      orgId,
      isDeleted: false,
      $or: [
        { userId }, // Owner
        {
          isShared: true,
          'sharedWith.userId': userId,
          ...(accessLevel === 'write' && { 'sharedWith.accessLevel': 'write' }),
        },
      ],
    });

    return conversation;
  } catch (error: any) {
    logger.error('Error validating agent conversation access', {
      conversationId,
      agentKey,
      userId,
      accessLevel,
      error: error.message,
    });
    return null;
  }
};

/**
 * Delete agent conversation (soft delete)
 */
export const deleteAgentConversation = async (
  conversationId: string,
  agentKey: string,
  userId: string,
  orgId: string,
): Promise<IChatSessionDocument | null> => {
  try {
    const conversation = await validateAgentConversationAccess(
      conversationId,
      agentKey,
      userId,
      orgId,
      'write',
    );

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
    throw error;
  }
};

/**
 * Initialize SSE response headers and send connection event
 */
export const initializeSSEResponse = (
  res: Response,
  protocol?: SSEProtocol,
): void => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Access-Control-Allow-Origin': '*',
    'X-Accel-Buffering': 'no',
  });

  res.write(
    isAGUI(protocol)
      ? frameAGUI(AGUIEventType.CUSTOM, {
          name: 'conversation_created',
          value: { message: 'SSE connection established' },
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

  const errorData: any = {
    error: errorMessage,
  };

  if (details) {
    errorData.details = details;
  }

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
  isAgentSession: boolean,
  protocol?: SSEProtocol,
): string => {
  const chunkStr = chunk.toString();
  let newBuffer = buffer + chunkStr;

  const events = newBuffer.split('\n\n');
  newBuffer = events.pop() || '';

  let filteredChunk = '';
  const agui = isAGUI(protocol);

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
      } else if (agui && eventType === AGUIEventType.RUN_ERROR && dataLine) {
        try {
          const errorData = JSON.parse(dataLine);
          if (existingConversation && messageId) {
            const errorMessage = errorData.message || 'Unknown error occurred';
            replaceMessageWithError(
              existingConversation,
              messageId,
              errorMessage,
              session,
              'streaming_error',
              errorData.stack,
            ).catch((err) => {
              logger.error('Failed to replace message with error', {
                requestId,
                error: err.message,
              });
            });
          }
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
            const toolCallMessage = {
              messageType: 'tool_call' as const,
              content: '',
              tools: [
                {
                  toolName: 'ask_user_question',
                  toolResult: eventData.value.toolData ?? eventData.value,
                },
              ],
              createdAt: new Date(),
              updatedAt: new Date(),
            };
            if (isAgentSession) {
              void appendMessages(
                existingConversation._id as mongoose.Types.ObjectId,
                existingConversation.orgId,
                [toolCallMessage],
              ).catch((saveErr: any) => {
                logger.error(
                  'Failed to persist ask_user_question tool_call message during regenerate',
                  {
                    requestId,
                    conversationId: existingConversation._id,
                    error: saveErr?.message,
                  },
                );
              });
            }
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
      } else if (!agui && eventType === 'error' && dataLine) {
        try {
          const errorData = JSON.parse(dataLine);
          if (existingConversation && messageId) {
            const errorMessage =
              errorData.error || errorData.message || 'Unknown error occurred';
            replaceMessageWithError(
              existingConversation,
              messageId,
              errorMessage,
              session,
              'streaming_error',
              errorData.stack,
              errorData.metadata
                ? new Map(Object.entries(errorData.metadata))
                : undefined,
            ).catch((err) => {
              logger.error('Failed to replace message with error', {
                requestId,
                error: err.message,
              });
            });
          }
          filteredChunk += event + '\n\n';
        } catch (parseError: any) {
          logger.error('Failed to parse error event data', {
            requestId,
            parseError: parseError.message,
            dataLine,
          });
          if (existingConversation && messageId) {
            const errorMessage = `Failed to parse error event: ${parseError.message}`;
            replaceMessageWithError(
              existingConversation,
              messageId,
              errorMessage,
              session,
              'parse_error',
              parseError.stack,
            ).catch((err) => {
              logger.error('Failed to replace message with error', {
                requestId,
                error: err.message,
              });
            });
          }
          filteredChunk += event + '\n\n';
        }
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
            const toolCallMessage = {
              messageType: 'tool_call' as const,
              content: '',
              tools: [
                {
                  toolName: 'ask_user_question',
                  toolResult: eventData.toolData ?? eventData,
                },
              ],
              createdAt: new Date(),
              updatedAt: new Date(),
            };
            if (isAgentSession) {
              void appendMessages(
                existingConversation._id as mongoose.Types.ObjectId,
                existingConversation.orgId,
                [toolCallMessage],
              ).catch((saveErr: any) => {
                logger.error(
                  'Failed to persist ask_user_question tool_call message during regenerate',
                  {
                    requestId,
                    conversationId: existingConversation._id,
                    error: saveErr?.message,
                  },
                );
              });
            }
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
): Promise<{
  conversation: any;
  savedCitations: ICitation[];
}> => {
  // Create and save citations
  const savedCitations: ICitation[] = await Promise.all(
    completeData.citations?.map(async (citation: ICitation) => {
      const newCitation = new Citation({
        content: citation.content,
        chunkIndex: citation.chunkIndex ?? 0,
        citationType: citation.citationType,
        metadata: {
          ...citation.metadata,
          orgId,
        },
      });
      return session ? newCitation.save({ session }) : newCitation.save();
    }) || [],
  );

  // Build AI response message and replace the original message with it,
  // preserving the original's _id/seq (see updateMessageById).
  const aiResponseMessage = buildAIResponseMessage(
    { statusCode: 200, data: completeData },
    savedCitations,
    modelInfo,
  );

  const updatedMessage = await updateMessageById(
    messageId,
    aiResponseMessage,
    session,
  );
  if (!updatedMessage) {
    throw new InternalServerError(
      'Failed to update conversation with regenerated response: message not found',
    );
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

  existingConversation.lastActivityAt = Date.now();
  existingConversation.status = CONVERSATION_STATUS.COMPLETE;

  // Save the updated conversation
  const updatedConversation = session
    ? await existingConversation.save({ session })
    : await existingConversation.save();

  if (!updatedConversation) {
    throw new InternalServerError(
      'Failed to update conversation with regenerated response',
    );
  }

  // Populate citationData across ALL messages so the frontend can rebuild its
  // citationMaps for the entire conversation. Otherwise previous messages lose
  // inline citation chips (see attachPopulatedCitations docstring).
  const responseConversation = await attachPopulatedCitations(
    updatedConversation.toObject(),
    [updatedMessage.toObject()],
    savedCitations,
    session,
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
): Promise<void> => {
  const errorMessage = error.message || 'Unknown error occurred';

  if (existingConversation && messageId) {
    try {
      await replaceMessageWithError(
        existingConversation,
        messageId,
        errorMessage,
        session,
        errorType,
        error.stack,
      );

      // Reload conversation to get updated state
      const updatedConversation = await ChatSession.findById(conversationId);
      if (updatedConversation) {
        const messages = await getMessages(
          updatedConversation._id as mongoose.Types.ObjectId,
          {},
          session,
        );
        const plainConversation = attachMessages(
          updatedConversation.toObject(),
          messages,
        );
        await sendSSEErrorEvent(
          res,
          errorMessage,
          error.message,
          plainConversation,
          protocol,
        );
      } else {
        await sendSSEErrorEvent(
          res,
          errorMessage,
          error.message,
          undefined,
          protocol,
        );
      }
    } catch (replaceError: any) {
      logger.error('Failed to replace message with error', {
        requestId,
        error: replaceError.message,
      });
      await sendSSEErrorEvent(
        res,
        errorMessage,
        error.message,
        undefined,
        protocol,
      );
    }
  } else {
    await sendSSEErrorEvent(
      res,
      errorMessage,
      error.message,
      undefined,
      protocol,
    );
  }
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
