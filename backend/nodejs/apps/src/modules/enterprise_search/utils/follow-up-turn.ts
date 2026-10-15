import mongoose, { Types } from 'mongoose';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  CONVERSATION_STATUS,
  EXCLUDE_AGENT,
  ONLY_AGENT,
} from '../constants/constants';
import { ChatSession } from '../schema/chat.session.schema';
import {
  IAIModel,
  IChatAttachmentRef,
  IChatSessionMessageDocument,
} from '../types/conversation.interfaces';
import { conversationGrantOf } from '../services/conversation/http/conversation-context';
import {
  inShortTransaction,
  TurnRun,
} from '../services/conversation/turn/turn-run';
import { ConversationTurnDeps } from '../services/conversation/turn/turn-deps';
import { conversationExtensions } from '../services/conversation/conversation-extensions';
import {
  AppendedTurn,
  FollowUpAppendInput,
  FollowUpContext,
  TurnBody,
} from '../services/conversation/turn/turn-extension';
import { loadProjectForSession } from './project-context';
import {
  appendMessages,
  buildUserQueryMessage,
  formatPreviousConversations,
  mirrorModelInfo,
  patchTurnSession,
} from './utils';

export type {
  AppendedTurn,
  FollowUpAppendInput,
  FollowUpContext,
} from '../services/conversation/turn/turn-extension';

/** The request fields that describe the user's message. */
export interface FollowUpBody extends Partial<IAIModel>, TurnBody {
  query: string;
  appliedFilters?: Parameters<typeof buildUserQueryMessage>[1];
  attachments?: IChatAttachmentRef[];
  resume?: { toolCallMessageId: string };
  [field: string]: unknown;
}

/** The user and org a turn runs as: the session user, or the scoped token's subject on an internal route. */
export function turnCallerOf(
  req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
): { userId: Types.ObjectId; orgId: Types.ObjectId } {
  const caller = (
    'user' in req
      ? (req as AuthenticatedUserRequest).user
      : (req as AuthenticatedServiceRequest).tokenPayload
  ) as { userId?: Types.ObjectId; orgId?: Types.ObjectId } | undefined;
  if (!caller?.userId) {
    throw new BadRequestError('User ID is required');
  }
  if (!caller.orgId) {
    throw new BadRequestError('Organization ID is required');
  }
  return { userId: caller.userId, orgId: caller.orgId };
}

const objectIdOf = (
  value: Types.ObjectId | string,
): Types.ObjectId | undefined =>
  Types.ObjectId.isValid(String(value))
    ? new Types.ObjectId(String(value))
    : undefined;

/** Appends the caller's `user_query` to an existing conversation and moves the session to `Inprogress`, as one write. */
export async function appendPlainFollowUp(
  input: FollowUpAppendInput,
): Promise<AppendedTurn> {
  const { req, target } = input;
  const body = input.body as FollowUpBody;
  const grant = conversationGrantOf(req);
  const orgId = new mongoose.Types.ObjectId(String(input.orgId));
  const conversation = await ChatSession.findOne({
    _id: grant.session._id,
    orgId,
    isDeleted: false,
    ...(target.kind === 'agent'
      ? { agentKey: target.agentKey, ...ONLY_AGENT }
      : EXCLUDE_AGENT),
  });
  if (!conversation) {
    throw new NotFoundError('Conversation not found');
  }

  conversation.status = CONVERSATION_STATUS.INPROGRESS;
  conversation.failReason = undefined;
  mirrorModelInfo(conversation, body as IAIModel);
  const now = Date.now();
  conversation.lastActivityAt = now;

  const authorUserId = objectIdOf(input.userId);
  const userMessage = buildUserQueryMessage(
    body.query,
    body.appliedFilters,
    body.chatMode,
    input.attachments,
    { authorUserId },
  );
  const sessionPatch = {
    status: CONVERSATION_STATUS.INPROGRESS,
    clearFailReason: true,
    lastActivityAt: now,
    modelInfo: body as IAIModel,
  };
  const userRow = await inShortTransaction(
    async (dbSession): Promise<IChatSessionMessageDocument> => {
      const [row] = await appendMessages(
        conversation._id,
        conversation.orgId,
        [userMessage],
        dbSession,
      );
      if (
        !(await patchTurnSession(conversation, sessionPatch, {}, dbSession))
      ) {
        throw new NotFoundError('Conversation not found');
      }
      return row as IChatSessionMessageDocument;
    },
  );
  return {
    conversation,
    userRow,
    run: { requestedBy: authorUserId, inReplyTo: userRow._id },
  };
}

/** What the AI backend is told about the turn: the rows before the user's message, and the owner's project scope. */
export async function plainFollowUpContext(
  turn: AppendedTurn,
  deps: ConversationTurnDeps,
): Promise<FollowUpContext> {
  const { conversation, userRow } = turn;
  const history = await deps.feed.historyBefore(conversation._id, userRow.seq);
  const project = await loadProjectForSession(
    conversation.orgId.toString(),
    (conversation.userId as unknown as Types.ObjectId).toString(),
    conversation.projectId,
  );
  return {
    previousConversations: formatPreviousConversations(history),
    project,
  };
}

/** Appends the caller's message to the conversation, through the turn extension. */
export const appendFollowUpQuery = (
  input: FollowUpAppendInput,
): Promise<AppendedTurn> =>
  conversationExtensions().turns.appendFollowUp(input);

/** What the AI backend is told about a follow-up, through the turn extension. */
export const followUpContext = (
  req: AuthenticatedUserRequest,
  turn: AppendedTurn,
  deps: ConversationTurnDeps,
): Promise<FollowUpContext> =>
  conversationExtensions().turns.followUpContext(req, turn, deps);

/** The request body for the AI backend: with a lease the Node-minted run id replaces the client's and `resume` has been checked; without one neither is trusted. */
export const aiRequestBody = (
  body: FollowUpBody,
  attachments: IChatAttachmentRef[] | undefined,
  run: TurnRun,
): Record<string, unknown> => {
  return {
    ...body,
    attachments,
    ...(run.lease ? { runId: run.lease.runId } : { resume: undefined }),
  };
};
