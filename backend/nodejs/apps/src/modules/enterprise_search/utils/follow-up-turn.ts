import type { TurnLabel } from '../../agent-harness/domain/turn-labels';
import { historyLabel } from '../../../libs/repositories/agent-harness/history-source/mongo';
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
  IMessage,
} from '../types/conversation.interfaces';
import { AppConfig } from '../../tokens_manager/config/config';
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
import {
  loadProjectForSession,
  projectCallerFromRequest,
} from './project-context';
import {
  appendMessages,
  buildUserQueryMessage,
  formatPreviousConversations,
  mirrorModelInfo,
  patchTurnSession,
} from './utils';
import { historyForModel, newReadable, viewerOf } from './viewer-redaction';
import {
  includableRows,
  type ReaderSession,
} from '../services/conversation/privacy/visibility';

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
      ? // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion -- tsc needs it: with Express's own `user` type, `in` does not narrow req
        (req as AuthenticatedUserRequest).user
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
    input.command,
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

/**
 * How a turn loads the owner's project when the owner is the caller: their teams count, and a
 * project they can no longer reach is unlinked. Without a request or `appConfig` neither applies,
 * so a team-only grant is not mistaken for lost access.
 */
export const ownerProjectOptions = (
  req: AuthenticatedUserRequest | undefined,
  deps: ConversationTurnDeps,
  conversationId: mongoose.Types.ObjectId,
): Parameters<typeof loadProjectForSession>[3] =>
  req && deps.appConfig
    ? { caller: projectCallerFromRequest(req, deps.appConfig), conversationId }
    : {};

/** What the AI backend is told about the turn: the rows before the user's message, and the owner's project scope. */
export async function plainFollowUpContext(
  turn: AppendedTurn,
  deps: ConversationTurnDeps,
  req?: AuthenticatedUserRequest,
): Promise<FollowUpContext> {
  const { conversation, userRow } = turn;
  // The history's access check runs while the project loads: both come before the first token.
  let label: TurnLabel | undefined;
  const [history, project] = await Promise.all([
    deps.feed
      .historyBefore(conversation._id, userRow.seq, { withLabels: true })
      .then((rows) => {
        // What the stored history read, as main's follow-up reads it: every row before the question.
        label = historyLabel(rows);
        return historyForSender(deps, turn, rows);
      }),
    loadProjectForSession(
      conversation.orgId.toString(),
      conversation.userId.toString(),
      conversation.projectId,
      ownerProjectOptions(req, deps, conversation._id),
    ),
  ]);
  return {
    previousConversations: formatPreviousConversations(history),
    ...(label !== undefined ? { historyLabel: label } : {}),
    project,
  };
}

/** The sender's check at the start of a turn gives up after this, and what it could not check goes empty. */
const SENDER_CHECK_TIMEOUT_MS = 5000;

/**
 * Stored rows as the model may be told them by the person who sent the turn: only the rows the reader check lets
 * reach their model (`includableRows`: their own answers while they can still see them, another person's only as
 * text when they replied to it or it restricts nobody), each sealed with its row and version, then main's filter
 * (`historyForModel`), so an answer that used a record they can no longer read goes with no text. Without
 * `deps.appConfig` the access check cannot run, and such an answer goes too.
 */
export const historyForSender = async <M extends IMessage>(
  deps: ConversationTurnDeps,
  turn: AppendedTurn,
  rows: M[],
  opts: { replyTargetId?: string; identity?: unknown } = {},
): Promise<M[]> => {
  const senderId = String(turn.run.requestedBy ?? turn.conversation.userId);
  const orgId = String(turn.conversation.orgId);
  const known = newReadable();
  const agentKey = (turn.conversation as { agentKey?: unknown }).agentKey;
  // Main's filter reads what each answer names (its citations, files and filters) before the seal cuts another
  // person's answer to its text; the two share one access request.
  const filtered = await historyForModel(
    deps.appConfig as AppConfig,
    viewerOf(senderId, orgId),
    rows,
    known,
  );
  return includableRows(
    {
      userId: senderId,
      orgId,
      member: true,
      ...(opts.identity !== undefined && { identity: opts.identity }),
    },
    turn.conversation as unknown as ReaderSession,
    filtered,
    {
      known,
      timeoutMs: SENDER_CHECK_TIMEOUT_MS,
      ...(deps.appConfig !== undefined && {
        appConfig: deps.appConfig,
      }),
      // The sender is running this chat's agent now: its own guard let them.
      ...(typeof agentKey === 'string' &&
        agentKey !== '' && { usableAgents: [agentKey] }),
      ...(opts.replyTargetId !== undefined && {
        replyTargetId: opts.replyTargetId,
      }),
    },
  );
};

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
