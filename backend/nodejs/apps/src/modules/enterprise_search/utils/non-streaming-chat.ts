import mongoose, { ClientSession } from 'mongoose';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { SERVICE_UNAVAILABLE_MESSAGE } from '../../../libs/errors/backend-error';
import {
  HttpError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import { CONVERSATION_STATUS } from '../constants/constants';
import { ChatSession } from '../schema/chat.session.schema';
import {
  AIServiceResponse,
  IAIModel,
  IAIResponse,
  IChatSession,
  IChatSessionDocument,
  IMessage,
} from '../types/conversation.interfaces';
import { AiChatRequest, ChatTarget } from './ai-chat-payload';
import {
  CHAT_ERROR_MESSAGES,
  userFacingAIResponseError,
  userFacingChatError,
} from './chat-error-messages';
import {
  appendMessages,
  formatPreviousConversations,
  getMessages,
  markAgentConversationFailed,
  markConversationFailed,
  saveCompleteConversation,
} from './utils';

/**
 * A non-streaming chat turn runs in three separate units of work:
 *   1. persist the user's message — one short transaction on a replica set;
 *   2. call the AI backend's non-streaming route, outside any transaction,
 *      because an LLM run can outlive MongoDB's 60 s transaction limit;
 *   3. persist the answer or the failure with the helpers the streaming
 *      routes use, so both paths leave identical conversations behind.
 * A crash between phases leaves an `Inprogress` conversation holding the
 * user's message — the same state a crashed streaming turn leaves.
 */

const logger = Logger.getInstance({ service: 'Non-streaming chat' });

/** Set on success and failure so a caller can find the conversation a failed turn left behind. */
export const CONVERSATION_ID_HEADER = 'X-Conversation-Id';

// Read per call: tests and the replica-set suite toggle it without reloading this module.
const isReplicaSet = (): boolean =>
  process.env.REPLICA_SET_AVAILABLE === 'true';

const inShortTransaction = async <T>(
  work: (session: ClientSession | null) => Promise<T>,
): Promise<T> => {
  if (!isReplicaSet()) return work(null);
  const session = await mongoose.startSession();
  try {
    let result: T | undefined;
    await session.withTransaction(async () => {
      result = await work(session);
    });
    return result as T;
  } finally {
    await session.endSession();
  }
};

const withSession = (
  session: ClientSession | null,
): { session: ClientSession } | undefined =>
  session ? { session } : undefined;

export const openConversation = (
  fields: Partial<IChatSession>,
  userMessage: IMessage,
): Promise<IChatSessionDocument> =>
  inShortTransaction(async (session) => {
    const conversation = (await new ChatSession(fields).save(
      withSession(session),
    )) as IChatSessionDocument;
    await appendMessages(
      conversation._id as mongoose.Types.ObjectId,
      conversation.orgId,
      [userMessage],
      session,
    );
    return conversation;
  });

export interface ContinuedConversation {
  conversation: IChatSessionDocument;
  /** History before this turn, in the shape the AI backend expects. */
  previousConversations: ReturnType<typeof formatPreviousConversations>;
}

export const continueConversation = (
  filter: Record<string, unknown>,
  userMessage: IMessage,
): Promise<ContinuedConversation> =>
  inShortTransaction(async (session) => {
    const conversation = await ChatSession.findOne(
      filter,
      null,
      withSession(session),
    );
    if (!conversation) {
      throw new NotFoundError('Conversation not found');
    }
    const history = await getMessages(
      conversation._id as mongoose.Types.ObjectId,
      {},
      session,
    );
    await appendMessages(
      conversation._id as mongoose.Types.ObjectId,
      conversation.orgId,
      [userMessage],
      session,
    );
    conversation.status = CONVERSATION_STATUS.INPROGRESS;
    conversation.failReason = undefined;
    conversation.lastActivityAt = Date.now();
    await conversation.save(withSession(session));
    return {
      conversation,
      previousConversations: formatPreviousConversations(history as IMessage[]),
    };
  });

const STATUS_CODE_NAMES: Record<number, string> = {
  400: 'BAD_REQUEST',
  401: 'UNAUTHORIZED',
  403: 'FORBIDDEN',
  404: 'NOT_FOUND',
  409: 'CONFLICT',
  413: 'PAYLOAD_TOO_LARGE',
  422: 'UNPROCESSABLE_ENTITY',
  424: 'FAILED_DEPENDENCY',
  429: 'TOO_MANY_REQUESTS',
};

/** A 4xx from the AI backend carries a message written for the user, so its status is kept. */
const rejectionFor = (statusCode: number, failReason: string): HttpError =>
  statusCode >= 400 && statusCode < 500
    ? new HttpError(
        STATUS_CODE_NAMES[statusCode] ?? 'AI_REQUEST_REJECTED',
        failReason,
        statusCode,
      )
    : new InternalServerError(failReason);

const causeCode = (error: unknown): unknown =>
  (error as { cause?: { code?: unknown } } | null)?.cause?.code;

/**
 * The error a thrown AI call sends back. A deliberate 4xx keeps its own
 * status and message; anything else carries only the user-facing reason.
 */
const clientErrorFor = (error: unknown, failReason: string): HttpError => {
  if (causeCode(error) === 'ECONNREFUSED') {
    return new InternalServerError(SERVICE_UNAVAILABLE_MESSAGE);
  }
  if (error instanceof HttpError && error.statusCode < 500) {
    return error;
  }
  return new InternalServerError(failReason);
};

const errorCodeOf = (data: unknown): string => {
  const code = (data as { code?: unknown } | null)?.code;
  return typeof code === 'string' && code.trim()
    ? code.trim()
    : 'ai_service_error';
};

const hasAnswer = (data: IAIResponse | undefined): data is IAIResponse =>
  !!data &&
  typeof data === 'object' &&
  ((typeof data.answer === 'string' && data.answer.trim() !== '') ||
    data.status === 'stopped');

export interface CompleteTurnOptions {
  target: ChatTarget;
  conversation: IChatSessionDocument;
  aiBackend: string;
  request: AiChatRequest;
  headers: Record<string, string>;
  modelInfo: IAIModel;
  requestId?: string;
}

export interface CompletedTurn {
  conversation: Record<string, unknown>;
  recordsUsed: number;
}

/** Phases 2 and 3. Throws the error the client should see once the failure is saved. */
export const completeTurn = async (
  options: CompleteTurnOptions,
): Promise<CompletedTurn> => {
  const { target, conversation, requestId } = options;
  const fail = async (
    failReason: string,
    errorType: string,
    clientError: HttpError,
    stack?: string,
  ): Promise<never> => {
    const markFailed =
      target.kind === 'agent'
        ? markAgentConversationFailed
        : markConversationFailed;
    try {
      await markFailed(conversation, failReason, null, errorType, stack);
    } catch (markError: unknown) {
      logger.error('Failed to record a failed non-streaming turn', {
        requestId,
        conversationId: String(conversation._id),
        error:
          markError instanceof Error ? markError.message : String(markError),
      });
    }
    throw clientError;
  };

  let response: AIServiceResponse<IAIResponse>;
  try {
    response = await new AIServiceCommand<IAIResponse>({
      uri: `${options.aiBackend}${options.request.path}`,
      method: HttpMethod.POST,
      headers: { ...options.headers, 'Content-Type': 'application/json' },
      body: options.request.payload,
      // An LLM run is not idempotent: a retried request would answer twice
      // and repeat any tool side effects.
      maxAttempts: 1,
    }).execute();
  } catch (error: unknown) {
    logger.error('AI backend call failed', {
      requestId,
      conversationId: String(conversation._id),
      error: error instanceof Error ? error.message : String(error),
    });
    const failReason = userFacingChatError(error);
    return fail(
      failReason,
      'internal_error',
      clientErrorFor(error, failReason),
      error instanceof Error ? error.stack : undefined,
    );
  }

  if (response.statusCode !== 200) {
    const failReason = userFacingAIResponseError(response);
    return fail(
      failReason,
      errorCodeOf(response.data),
      rejectionFor(response.statusCode, failReason),
    );
  }
  if (!hasAnswer(response.data)) {
    return fail(
      CHAT_ERROR_MESSAGES.failed,
      'no_response',
      new InternalServerError(CHAT_ERROR_MESSAGES.failed),
    );
  }

  try {
    const saved = (await saveCompleteConversation(
      conversation,
      response.data,
      String(conversation.orgId),
      null,
      options.modelInfo,
    )) as Record<string, unknown>;
    return {
      conversation: saved,
      // Python omits `citations` on some agent answers despite the type.
      recordsUsed: Array.isArray(response.data.citations)
        ? response.data.citations.length
        : 0,
    };
  } catch (error: unknown) {
    return fail(
      CHAT_ERROR_MESSAGES.saveFailed,
      'save_error',
      new InternalServerError(CHAT_ERROR_MESSAGES.saveFailed),
      error instanceof Error ? error.stack : undefined,
    );
  }
};
