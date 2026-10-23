import {
  AIServiceResponse,
  IAIModel,
  IAppliedFilters,
  IChatAttachmentRef,
  IMessage,
  IMessageCitation,
  IMessageDocument,
  IMessagePart,
} from '../types/conversation.interfaces';
import {
  getConversationRepository,
  type ChatSessionDto,
  type CitationDto,
  type MessageDto,
  type NewMessage,
  type NewSessionError,
  type RunReservation,
  type RunStateChange,
  type SessionActor,
  type SessionListQuery,
  type SessionScope,
} from '../../../libs/repositories/conversation';
import { idOf, isValidId } from '../../../libs/repositories/ids';
import type { Tx } from '../../../libs/repositories/uow';
import { IAIResponse } from '../types/conversation.interfaces';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  InternalServerError,
} from '../../../libs/errors/http.errors';
import {
  CONVERSATION_STATUS,
  REASONING_EFFORT_VALUES,
} from '../constants/constants';
import { Logger } from '../../../libs/services/logger.service';
import { Response } from 'express';
import { getUserRepository } from '../../../libs/repositories/user';
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
import {
  CHAT_ERROR_MESSAGES,
  userFacingChatError,
} from './chat-error-messages';
import { stripModelAuthoredMarkers } from './answer-markers';

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
  appliedFilters?: IAppliedFilters,
  chatMode?: string,
  attachments?: IChatAttachmentRef[],
): NewMessage => ({
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
): Promise<string[]> => {
  const { ids, truncated } = await getConversationRepository().findIdsByContent(
    orgId,
    escapedSearch,
    limit,
  );
  if (truncated) {
    logger.warn(
      'Chat content search was cut short; older matches may be missing',
      { orgId, matched: ids.length },
    );
  }
  return ids;
};

/**
 * A regeneration that edits the chat's first question moves the title with it, in memory, and whatever saves the chat's
 * state next (the answer, a partial answer, a failure) writes that title with it; restoring the title drops the entry.
 */
export const editedTitles = new WeakMap<object, string>();

const editedTitleOf = (conversation: object): { title?: string } => {
  const title = editedTitles.get(conversation);
  return title === undefined ? {} : { title };
};

export const buildAIFailureResponseMessage = (
  content?: string,
): NewMessage => ({
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
 * Append messages to a session (their `seq` block is allocated first, then
 * they are inserted, see the repository).
 */
export const appendMessages = (
  sessionId: string,
  orgId: string,
  messages: NewMessage[],
  tx?: Tx | null,
): Promise<MessageDto[]> =>
  getConversationRepository().appendMessages(
    idOf(sessionId),
    idOf(orgId),
    messages,
    tx ?? undefined,
  );

/** Wholesale-replace one message, keeping its id, position and author (regeneration). */
export const updateMessageById = (
  messageId: string,
  orgId: string,
  newContent: NewMessage,
  tx?: Tx | null,
): Promise<MessageDto | null> =>
  getConversationRepository().replaceMessage(
    idOf(messageId),
    idOf(orgId),
    newContent,
    tx ?? undefined,
  );

/**
 * Rewrite a stored `user_query` row, used when the user edits the last
 * question and regenerates its answer. Only `content` changes, and
 * `attachments` when the edit replaced them (omitted keeps the stored files);
 * mentions and seq stay as they were. Scoped to the conversation, so an id
 * from anywhere else matches nothing.
 */
export const updateUserQueryContent = (
  messageId: string,
  content: string,
  sessionId: string,
  orgId: string,
  tx?: Tx | null,
  attachments?: IChatAttachmentRef[],
): Promise<MessageDto | null> =>
  getConversationRepository().updateUserQueryContent(
    idOf(messageId),
    idOf(orgId),
    idOf(sessionId),
    content,
    attachments,
    tx ?? undefined,
  );

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

export const deleteMessagesById = (
  messageIds: string[],
  orgId: string,
  tx?: Tx | null,
  sessionId?: string,
): Promise<number> =>
  getConversationRepository().deleteMessages(
    messageIds.map(idOf),
    idOf(orgId),
    tx ?? undefined,
    sessionId ? idOf(sessionId) : undefined,
  );

/** Append a feedback entry to one message's `feedback` array. */
export const appendMessageFeedback = (
  messageId: string,
  orgId: string,
  feedbackEntry: unknown,
  tx?: Tx | null,
): Promise<MessageDto | null> =>
  getConversationRepository().appendFeedback(
    idOf(messageId),
    idOf(orgId),
    feedbackEntry,
    tx ?? undefined,
  );

/**
 * Fetch a session's messages, `seq`-ordered (ascending = chronological).
 */
export const getMessages = async (
  sessionId: string,
  options: {
    orgId: string;
    skip?: number;
    limit?: number;
    populateCitations?: boolean;
    sort?: 1 | -1;
  },
  tx?: Tx | null,
): Promise<any[]> => {
  const rows = await getConversationRepository().listMessages(
    idOf(sessionId),
    {
      orgId: idOf(options.orgId),
      skip: options.skip,
      limit: options.limit,
      withCitations: options.populateCitations,
      order: options.sort === -1 ? 'desc' : 'asc',
    },
    tx ?? undefined,
  );
  return rows.map((row) => row.dto);
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
 * Error text, the failure reason and the compacted summary are written from the
 * owner's runs and can quote what they read; other people listing the chat get none.
 */
export const withoutOwnerOnlyFields = <T extends object>(
  conversation: T,
): T => {
  const {
    conversationErrors: _errors,
    failReason: _reason,
    compactedSummary: _summary,
    ...visible
  } = conversation as Record<string, unknown>;
  return visible as T;
};

export const attachMessages = (session: any, messages: any[]): any => {
  const { nextSeq, sessionType, ...cleanSession } = session ?? {};
  return {
    ...withoutErrorStacks(cleanSession as object),
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
  fallbackCitations: CitationDto[],
  tx?: Tx | null,
): Promise<any> => {
  const sessionId = session?._id;
  let messages = fallbackMessages;

  // Only attempt the populate round-trip when Mongoose is actually connected.
  // In unit tests (and any environment without an active DB connection) the
  // default Mongoose buffering would hang this call for ~10s before failing,
  // which is both slow and unnecessary — the fallback branch handles those
  // cases using the caller-supplied messages.
  const isConnected = getConversationRepository().isConnected();

  if (sessionId && isConnected) {
    try {
      const { orgId } = session as { orgId: string };
      messages = await getMessages(
        sessionId,
        { orgId, populateCitations: true },
        tx,
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
            (CitationDto & { _id?: string }) | string | undefined;
          // Ids cross the repository as strings, so an object here is a citation row
          // (an ObjectId must be excluded: some bson versions give it an inherited `_id`).
          const isPopulatedCitationDoc =
            !!populated &&
            typeof populated === 'object' &&
            (populated as { _bsontype?: string })._bsontype !== 'ObjectId' &&
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
                  (c: CitationDto) => idOf(c._id) === idOf(citation.citationId),
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

export const buildAIResponseMessage = (
  aiResponse: AIServiceResponse<IAIResponse>,
  citations: CitationDto[] = [],
  modelInfo?: IAIModel,
): NewMessage => {
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

  const message: NewMessage = {
    messageType: isClassifiedFailureAnswer(data) ? 'error' : 'bot_response',
    createdAt: new Date(),
    updatedAt: new Date(),
    content: data.answer ?? '',
    contentFormat: 'MARKDOWN',
    citations: citations.map((citation) => ({
      citationId: citation._id,
    })),
    confidence: data.confidence,
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
  message: NewMessage,
  payload: unknown,
): NewMessage => {
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

type PreviousToolResult = ReturnType<typeof toolResultsFromParts>[number];

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

/** Whether the turn at `index` still gets an answer before the next question. */
const answerFollows = (messages: IMessage[], index: number): boolean => {
  for (let j = index + 1; j < messages.length; j++) {
    const type = messages[j]?.messageType;
    if (type === 'bot_response') return true;
    if (type === 'user_query') return false;
  }
  return false;
};

/**
 * Prior turns for the AI service, oldest first.
 *
 * A turn that failed before an answer leaves an `error` row in place of one.
 * It is sent as an answer with `status: 'failed'`: dropped, its question
 * replayed as still open, and providers that merge consecutive user turns
 * joined it to the current question. An answer the user stopped carries
 * `status: 'stopped'`. The AI service renders both as short markers.
 */
export const formatPreviousConversations = (messages: IMessage[]) => {
  const result: Array<Record<string, unknown>> = [];
  // Tracked, not read off the last entry: a fork keeps stored `system` rows,
  // which can sit between a question and its error.
  let questionOpen = false;
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (!msg || msg.messageType === 'tool_call') {
      continue;
    }
    if (msg.messageType === 'error') {
      if (questionOpen && !answerFollows(messages, i)) {
        result.push({ role: 'bot_response', content: '', status: 'failed' });
        questionOpen = false;
      }
      continue;
    }
    if (msg.messageType === 'user_query') questionOpen = true;
    if (msg.messageType === 'bot_response') questionOpen = false;
    let toolResults: PreviousToolResult[] =
      msg.messageType === 'bot_response' ? toolResultsFromParts(msg.parts) : [];
    if (msg.messageType === 'bot_response') {
      // The questions row is a sibling of the answer, not part of its `parts`;
      // a resume needs it on this turn.
      toolResults = withTurnAskToolResults(messages, i, toolResults);
    }
    result.push({
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

export const addComputedFields = <
  T extends {
    initiator: { toString(): string };
    sharedWith?: Array<{ userId: { toString(): string }; accessLevel: string }>;
  },
>(
  conversation: T,
  userId: string | { toString(): string },
) => {
  // Callers may hand over an ObjectId where a string is typed.
  const viewerId = String(userId);
  const isOwner = conversation.initiator.toString() === viewerId;
  // Owner-run fields are for the owner only, in every list a shared chat appears in.
  return {
    ...withoutErrorStacks(
      isOwner ? conversation : withoutOwnerOnlyFields(conversation),
    ),
    isOwner,
    accessLevel:
      conversation.sharedWith?.find(
        (share) => share.userId.toString() === viewerId,
      )?.accessLevel || 'read',
  };
};

export type SharedByInfo = {
  userId: string;
  name: string;
};

function sharedByDisplayName(user: {
  fullName?: string;
  firstName?: string;
  lastName?: string;
  email?: string;
}): string {
  const fullName = user.fullName?.trim();
  if (fullName) return fullName;
  const parts = [user.firstName, user.lastName]
    .filter((part): part is string => Boolean(part?.trim()))
    .join(' ')
    .trim();
  if (parts) return parts;
  return user.email?.trim() || '';
}

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
          return isValidId(id);
        }),
    ),
  ];

  if (initiatorIds.length === 0) {
    return conversations;
  }

  const users = await getUserRepository().listNamesByIds(orgId, initiatorIds);

  const userById = new Map(users.map((user) => [user._id, user] as const));

  return conversations.map((conversation) => {
    const initiatorId = conversation.initiator?.toString();
    if (!initiatorId) {
      return conversation;
    }
    if (conversationIsOwnedByCaller(conversation)) {
      return conversation;
    }
    const user = userById.get(initiatorId);
    const name = user ? sharedByDisplayName(user) : '';
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
  conversation: ChatSessionDto,
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
    projectId: conversation.projectId,
    projectVisibility: conversation.projectVisibility,
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
      isOwner: idOf(conversation.initiator) === idOf(userId),
      accessLevel:
        conversation.sharedWith?.find(
          (share) => idOf(share.userId) === idOf(userId),
        )?.accessLevel || 'read',
    },
  };
};

const repo = (): ReturnType<typeof getConversationRepository> =>
  getConversationRepository();

/**
 * Saves a finished answer: its citations, the answer message and the session's
 * new state, in that order (a crash between leaves a recoverable Inprogress).
 * `messageModelInfo` is stamped on the message; `sessionModelInfo` also updates
 * the session's own.
 */
export const persistCompletedAnswer = async (
  conversation: ChatSessionDto,
  completeData: IAIResponse,
  orgId: string,
  tx: Tx | null | undefined,
  options: {
    messageModelInfo?: IAIModel;
    sessionModelInfo?: IAIModel;
    failureLabel: string;
  },
): Promise<{
  conversation: any;
  citations: CitationDto[];
  message: MessageDto;
}> => {
  // Save citations first
  const citations = await repo().createCitations(
    completeData.citations?.map((citation: any) => ({
      content: citation.content,
      chunkIndex: citation.chunkIndex,
      citationType: citation.citationType,
      metadata: {
        ...citation.metadata,
        orgId,
      },
    })) ?? [],
    tx ?? undefined,
  );

  // Create AI response message
  const aiResponseMessage = buildAIResponseMessage(
    { data: completeData, statusCode: 200 },
    citations,
    options.messageModelInfo,
  );

  const [insertedMessage] = await appendMessages(
    conversation._id,
    conversation.orgId,
    [aiResponseMessage],
    tx,
  );

  const updatedConversation = await repo().recordRunState(
    conversation._id,
    {
      ...(options.sessionModelInfo
        ? { modelInfo: options.sessionModelInfo }
        : {}),
      ...editedTitleOf(conversation),
      lastActivityAt: Date.now(),
      ...classifiedFailureChange(completeData),
    },
    tx ?? undefined,
  );
  if (!updatedConversation) {
    throw new InternalServerError(options.failureLabel);
  }

  return {
    conversation: updatedConversation,
    citations,
    message: insertedMessage!,
  };
};

const persistAnswer = async (
  conversation: ChatSessionDto,
  completeData: IAIResponse,
  orgId: string,
  tx: Tx | null | undefined,
  modelInfo: IAIModel | undefined,
  label: string,
): Promise<unknown> => {
  const saved = await persistCompletedAnswer(
    conversation,
    completeData,
    orgId,
    tx,
    {
      messageModelInfo: modelInfo,
      sessionModelInfo: modelInfo,
      failureLabel: label,
    },
  );
  return attachPopulatedCitations(
    saved.conversation,
    [saved.message],
    saved.citations,
    tx,
  );
};

// Helper function to save complete conversation
export const saveCompleteConversation = async (
  conversation: ChatSessionDto,
  completeData: IAIResponse,
  orgId: string,
  session?: Tx | null,
  modelInfo?: IAIModel,
): Promise<any> => {
  try {
    return await persistAnswer(
      conversation,
      completeData,
      orgId,
      session,
      modelInfo,
      'Failed to update conversation',
    );
  } catch (error: any) {
    logger.error('Error saving complete conversation', {
      conversationId: conversation._id,
      error: error.message,
    });
    throw error;
  }
};

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
  return {
    message: errorMessage,
    errorType: aguiCode,
    messageId,
    stack,
    metadata: mergedMetadata,
  };
};

/**
 * A run is only believed live this long after its last heartbeat, so a crashed
 * Node frees the thread soon. Well above the heartbeat so a few missed beats
 * never hand a live run's thread to a second writer.
 */
const RUN_HEARTBEAT_MS = 60 * 1000;

export type { RunReservation };

/**
 * Claims the thread for a new run in one atomic update, so two requests that
 * arrive together cannot both start one. Waits briefly when a run is going,
 * then refuses with a conflict; null when no session matches.
 */
export const reserveRun = async (
  scope: SessionScope,
  conversationId: string,
  actor: SessionActor,
  settleMs?: number,
): Promise<RunReservation | null> => {
  return repo().reserveRun(
    scope,
    conversationId,
    { orgId: actor.orgId, userId: actor.userId },
    settleMs,
  );
};

/**
 * Gives the thread back when a run that was reserved never got going, or as a
 * safety net once one has finished. Best effort: it must not mask the error
 * being handled, and the activity window frees a thread this misses.
 */
export const releaseRun = async (
  reservation: RunReservation | null,
): Promise<void> => {
  if (!reservation) return;
  try {
    await repo().releaseRun(reservation);
  } catch (error: unknown) {
    logger.warn('Failed to release the run reservation', {
      sessionId: reservation.sessionId,
      error: error instanceof Error ? error.message : String(error),
    });
  }
};

/**
 * Keeps `lastActivityAt` fresh while a run's AI stream is open, so a long
 * answer, or a run whose browser left, is never mistaken for a crashed one by
 * `reserveRun`. Stops by itself when the stream closes, however it ends.
 */
export const keepRunAlive = (
  stream: { once(event: 'close', listener: () => void): unknown },
  sessionId: string,
  runId?: string,
): void => {
  const timer = setInterval(() => {
    void (async () => {
      try {
        await repo().touchRun(sessionId, runId);
      } catch (error: unknown) {
        logger.warn('Failed to record run activity', {
          sessionId: sessionId,
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

/** Inserts the failure message, then marks the session failed (chat and agent chats alike). */
const failConversation = async (
  conversation: ChatSessionDto,
  failReason: string,
  tx: Tx | null | undefined,
  errorType: string | undefined,
  stack: string | undefined,
  metadata: Map<string, any> | undefined,
  noun: string,
): Promise<void> => {
  const failedMessage = buildAIFailureResponseMessage(failReason);
  await appendMessages(
    conversation._id,
    conversation.orgId,
    [failedMessage],
    tx,
  );

  const savedWithError = await repo().recordRunState(
    conversation._id,
    {
      status: CONVERSATION_STATUS.FAILED,
      failReason,
      ...editedTitleOf(conversation),
      lastActivityAt: Date.now(),
      appendError: buildSessionError(
        failReason,
        errorType,
        undefined,
        stack,
        metadata,
      ),
    },
    tx ?? undefined,
  );

  if (!savedWithError) {
    logger.error(`Failed to save ${noun} error state`, {
      conversationId: conversation._id,
      agentKey: conversation.agentKey,
      failReason,
    });
  }
};

export const markConversationFailed = async (
  conversation: ChatSessionDto,
  failReason: string,
  session?: Tx | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, any>,
): Promise<void> => {
  try {
    await failConversation(
      conversation,
      failReason,
      session,
      errorType,
      stack,
      metadata,
      'conversation',
    );
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
 * Persists the partial answer of a run Node gave up on: its browser went away
 * and it outlived the detached-run cap (see `attachUpstreamAbort`), so Python
 * never sent a terminal `RUN_FINISHED`. `replaceMessageId` is set for the
 * regenerate path, which replaces the original message instead of appending.
 */
export const savePartialConversation = async (
  conversation: ChatSessionDto,
  partialText: string,
  session?: Tx | null,
  options?: {
    replaceMessageId?: string;
    parts?: IMessagePart[];
  },
): Promise<void> => {
  try {
    const partialMessage: IMessage = {
      messageType: 'bot_response',
      content: stripModelAuthoredMarkers(partialText),
      contentFormat: 'MARKDOWN',
      status: 'stopped',
      ...(options?.parts?.length ? { parts: options.parts } : {}),
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    if (options?.replaceMessageId) {
      const updated = await updateMessageById(
        options.replaceMessageId,
        conversation.orgId,
        partialMessage as NewMessage,
        session,
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
        [partialMessage as NewMessage],
        session,
      );
    }

    const saved = await repo().recordRunState(
      conversation._id,
      {
        status: CONVERSATION_STATUS.STOPPED,
        ...editedTitleOf(conversation),
        lastActivityAt: Date.now(),
      },
      session ?? undefined,
    );

    if (!saved) {
      logger.error('Failed to save conversation after partial stop', {
        conversationId: conversation._id,
      });
    }
  } catch (error: any) {
    logger.error('Error saving partial conversation', {
      conversationId: conversation._id,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Replace a message (identified by its `_id`) with an error message, used
 * for regeneration.
 */
export const replaceMessageWithError = async (
  conversation: ChatSessionDto,
  messageId: string,
  errorMessage: string,
  session?: Tx | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, any>,
): Promise<void> => {
  try {
    // Replace the message with an error message, preserving its _id/seq
    const failedMessage = buildAIFailureResponseMessage(errorMessage);
    const updatedMessage = await updateMessageById(
      messageId,
      conversation.orgId,
      failedMessage,
      session,
    );
    if (!updatedMessage) {
      logger.error('Failed to replace message with error: message not found', {
        conversationId: conversation._id,
        messageId,
      });
    }

    const savedWithError = await repo().recordRunState(
      conversation._id,
      {
        status: CONVERSATION_STATUS.FAILED,
        failReason: errorMessage,
        ...editedTitleOf(conversation),
        lastActivityAt: Date.now(),
        appendError: buildSessionError(
          errorMessage,
          errorType,
          messageId,
          stack,
          metadata,
        ),
      },
      session ?? undefined,
    );

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
  conversation: ChatSessionDto,
  completeData: IAIResponse,
  orgId: string,
  session?: Tx | null,
  modelInfo?: IAIModel,
): Promise<any> => {
  try {
    return await persistAnswer(
      conversation,
      completeData,
      orgId,
      session,
      modelInfo,
      'Failed to update agent conversation',
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
  conversation: ChatSessionDto,
  failReason: string,
  session?: Tx | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, any>,
): Promise<void> => {
  try {
    await failConversation(
      conversation,
      failReason,
      session,
      errorType,
      stack,
      metadata,
      'agent conversation',
    );
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
export const validateAgentConversationAccess = (
  conversationId: string,
  agentKey: string,
  userId: string,
  orgId: string,
  accessLevel: 'read' | 'write' = 'read',
): Promise<ChatSessionDto | null> =>
  repo().findAgentWithAccess(
    conversationId,
    agentKey,
    { orgId: orgId, userId: userId },
    accessLevel,
  );

/**
 * Delete agent conversation (soft delete)
 */
export const deleteAgentConversation = async (
  conversationId: string,
  agentKey: string,
  userId: string,
  orgId: string,
  // Runs once access is confirmed and before the delete; a throw keeps the conversation.
  beforeDelete?: () => Promise<void>,
): Promise<ChatSessionDto | null> => {
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

    await beforeDelete?.();

    const updatedConversation = await repo().softDeleteAgent(
      conversation._id,
      userId,
    );

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
    'Content-Type': 'text/event-stream; charset=utf-8',
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

/**
 * Handle regeneration stream data events
 */
export const handleRegenerationStreamData = (
  chunk: Buffer,
  buffer: string,
  existingConversation: ChatSessionDto | null,
  messageId: string | null,
  session: Tx | null,
  requestId: string,
  res: Response,
  onCompleteData: (data: IAIResponse) => void,
  protocol?: SSEProtocol,
  accumulator?: StreamedContentAccumulator,
  onUpstreamError?: () => void,
  onAskUserQuestion?: (payload: unknown) => void,
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
      if (agui && eventType === AGUIEventType.TOOL_CALL_START && dataLine) {
        accumulator?.feedToolCallStart(dataLine);
      }

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
      } else if (
        agui &&
        eventType === AGUIEventType.TEXT_MESSAGE_CONTENT &&
        dataLine
      ) {
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
          const errorData = JSON.parse(dataLine);
          onUpstreamError?.();
          if (existingConversation && messageId) {
            const errorMessage =
              errorData.message || CHAT_ERROR_MESSAGES.failed;
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
            const payload = eventData.value.toolData ?? eventData.value;
            onAskUserQuestion?.(payload);
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
          const errorData = JSON.parse(dataLine);
          if (existingConversation && messageId) {
            const errorMessage =
              errorData.error ||
              errorData.message ||
              CHAT_ERROR_MESSAGES.failed;
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
            const errorMessage = CHAT_ERROR_MESSAGES.failed;
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
  existingConversation: ChatSessionDto,
  messageId: string,
  orgId: string,
  session: Tx | null,
  modelInfo?: IAIModel,
  askUserQuestionPayload?: unknown,
  staleAskToolCallIds?: string[],
): Promise<{
  conversation: any;
  savedCitations: CitationDto[];
}> => {
  // Create and save citations
  const savedCitations = await repo().createCitations(
    completeData.citations?.map((citation) => ({
      content: citation.content,
      chunkIndex: citation.chunkIndex ?? 0,
      citationType: citation.citationType,
      metadata: {
        ...citation.metadata,
        orgId,
      },
    })) ?? [],
    session ?? undefined,
  );

  // Build AI response message and replace the original message with it,
  // preserving the original's _id/seq (see updateMessageById).
  const aiResponseMessage = buildAIResponseMessage(
    { statusCode: 200, data: completeData },
    savedCitations,
    modelInfo,
  );
  if (askUserQuestionPayload) {
    attachAskUserQuestionToMessage(aiResponseMessage, askUserQuestionPayload);
  }

  const updatedMessage = await updateMessageById(
    messageId,
    existingConversation.orgId,
    aiResponseMessage,
    session,
  );
  if (!updatedMessage) {
    throw new InternalServerError(
      'Failed to update conversation with regenerated response: message not found',
    );
  }

  // The replacement answer carries its own questions payload, so the rows the
  // discarded run left beside it are stale. Non-fatal: an answer the user can
  // already see must not fail on transcript housekeeping.
  if (staleAskToolCallIds?.length) {
    try {
      await deleteMessagesById(
        staleAskToolCallIds,
        existingConversation.orgId,
        session,
        existingConversation._id,
      );
    } catch (cleanupErr: any) {
      logger.warn('Failed to drop stale rows after regenerate', {
        conversationId: existingConversation._id,
        messageId,
        error: cleanupErr?.message,
      });
    }
  }

  // Save the updated conversation
  const updatedConversation = await repo().recordRunState(
    existingConversation._id,
    {
      ...(modelInfo ? { modelInfo } : {}),
      ...editedTitleOf(existingConversation),
      lastActivityAt: Date.now(),
      ...classifiedFailureChange(completeData),
    },
    session ?? undefined,
  );

  if (!updatedConversation) {
    throw new InternalServerError(
      'Failed to update conversation with regenerated response',
    );
  }

  // Populate citationData across ALL messages so the frontend can rebuild its
  // citationMaps for the entire conversation. Otherwise previous messages lose
  // inline citation chips (see attachPopulatedCitations docstring).
  const responseConversation = await attachPopulatedCitations(
    updatedConversation,
    [updatedMessage],
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
  existingConversation: ChatSessionDto | null,
  messageId: string | null,
  conversationId: string,
  session: Tx | null,
  requestId: string,
  errorType: string = 'regeneration_error',
  protocol?: SSEProtocol,
): Promise<void> => {
  const errorMessage = userFacingChatError(error);

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
      const updatedConversation = await repo().findById(conversationId);
      if (updatedConversation) {
        const messages = await getMessages(
          updatedConversation._id,
          { orgId: updatedConversation.orgId },
          session,
        );
        const plainConversation = attachMessages(updatedConversation, messages);
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
    const stages = this.marks
      .map(([n, ms]) => `${n}=${ms.toFixed(0)}ms`)
      .join(' ');
    logger.info(
      `⏱ ${label} total=${this.totalMs.toFixed(0)}ms | ${stages}`,
      extra,
    );
  }
}
