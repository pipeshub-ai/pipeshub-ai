import mongoose, { Types } from 'mongoose';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { IProjectDocument } from '../../projects/types/project.interfaces';
import {
  CONVERSATION_STATUS,
  EXCLUDE_AGENT,
  ONLY_AGENT,
} from '../constants/constants';
import { ChatSession } from '../schema/chat.session.schema';
import {
  IAIModel,
  IChatAttachmentRef,
  IChatSessionDocument,
  IChatSessionMessageDocument,
  IMessage,
} from '../types/conversation.interfaces';
import {
  conversationContextOf,
  conversationGrantOf,
} from '../services/collaboration/http/conversation-context';
import { assertNotDuplicate } from '../services/collaboration/http/turn-preconditions';
import { fencedWrite } from '../services/collaboration/leases/fenced-write';
import { TurnGate } from '../services/collaboration/turn/turn-gate';
import {
  inShortTransaction,
  TurnRun,
} from '../services/collaboration/turn/turn-run';
import { ConversationTurnDeps } from '../services/collaboration/turn/turn-deps';
import { isCollaborative } from '../services/collaboration/http/resume-binding';
import {
  buildTurnRoster,
  CollaborationPayload,
  TurnRoster,
} from '../services/collaboration/turn/participant-roster';
import { notifiableMentions } from '../services/collaboration/notify/mention-principals';
import { conversationEventProducers } from '../services/collaboration/notify/conversation-event-producers';
import { callerIdentityOf } from '../../../libs/types/caller-identity';
import { Logger } from '../../../libs/services/logger.service';
import { MentionRef } from '../services/collaboration/mentions/mention.types';
import {
  toWireMentions,
  WireMention,
} from '../services/collaboration/turn/mention-refs';
import {
  turnGuestAgentOf,
  turnMentionsOf,
} from '../services/collaboration/mentions/turn-mentions';
import {
  agentProfiles,
  IAgentProfiles,
} from '../services/collaboration/mentions/agent.directory';
import { respondingAgentViews } from '../services/collaboration/mentions/responding-agent';
import { ChatTarget } from './ai-chat-payload';
import { loadProjectForSession } from './project-context';
import {
  appendMessages,
  buildUserQueryMessage,
  formatPreviousConversations,
  mirrorModelInfo,
  patchTurnSession,
} from './utils';

const logger = Logger.getInstance({ service: 'FollowUpTurn' });

/** The request fields that describe the user's message. */
export interface FollowUpBody extends Partial<IAIModel> {
  query: string;
  appliedFilters?: Parameters<typeof buildUserQueryMessage>[1];
  attachments?: IChatAttachmentRef[];
  clientMessageId?: string;
  filesShared?: boolean;
  shareToolResults?: boolean;
  resume?: { toolCallMessageId: string };
  mentions?: unknown;
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

export interface FollowUpAppendInput {
  req: AuthenticatedUserRequest;
  target: ChatTarget;
  userId: Types.ObjectId | string;
  orgId: Types.ObjectId | string;
  body: FollowUpBody;
  attachments?: IChatAttachmentRef[];
  gate: TurnGate;
}

export interface AppendedTurn {
  conversation: IChatSessionDocument;
  userRow: IChatSessionMessageDocument;
  run: TurnRun;
}

const objectIdOf = (
  value: Types.ObjectId | string,
): Types.ObjectId | undefined =>
  Types.ObjectId.isValid(String(value))
    ? new Types.ObjectId(String(value))
    : undefined;

/** A unique-key violation (11000) on the per-author `clientMessageId` index. */
const isDuplicateClientMessage = (error: unknown): boolean => {
  const e = error as {
    code?: unknown;
    keyPattern?: Record<string, unknown>;
    message?: unknown;
  } | null;
  return (
    e?.code === 11000 &&
    (e.keyPattern?.clientMessageId !== undefined ||
      /clientMessageId/.test(String(e.message)))
  );
};

/**
 * Appends the caller's `user_query` to an existing conversation and moves the session to
 * `Inprogress`, as one write: fenced on the lease with the flag on. A concurrent send of the same
 * `clientMessageId` that slipped past the guard's pre-check lands here as a unique violation and is
 * rethrown as `DuplicateMessageError`.
 */
export async function appendFollowUpQuery(
  input: FollowUpAppendInput,
): Promise<AppendedTurn> {
  const { req, target, body, gate } = input;
  const lease = gate.lease;
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
  const mentions = turnMentionsOf(req);
  const guestAgentKey = turnGuestAgentOf(req);
  const userMessage = buildUserQueryMessage(
    body.query,
    body.appliedFilters,
    body.chatMode,
    input.attachments,
    {
      authorUserId,
      ...(lease && {
        clientMessageId: body.clientMessageId,
        filesShared: body.filesShared,
        shareToolResults: body.shareToolResults,
        runId: lease.runId,
        ...(mentions.length > 0 && { mentions: [...mentions] }),
      }),
    },
  );
  const sessionPatch = {
    status: CONVERSATION_STATUS.INPROGRESS,
    clearFailReason: true,
    lastActivityAt: now,
    modelInfo: body as IAIModel,
  };
  const write = async (
    dbSession: Parameters<Parameters<typeof inShortTransaction>[0]>[0],
  ): Promise<IChatSessionMessageDocument> => {
    const [row] = await appendMessages(
      conversation._id,
      conversation.orgId,
      [userMessage],
      dbSession,
    );
    if (
      !(await patchTurnSession(
        conversation,
        sessionPatch,
        { lease },
        dbSession,
      ))
    ) {
      throw new NotFoundError('Conversation not found');
    }
    return row as IChatSessionMessageDocument;
  };

  let userRow: IChatSessionMessageDocument;
  try {
    userRow = lease
      ? await fencedWrite(lease, write)
      : await inShortTransaction(write);
  } catch (error) {
    if (lease && body.clientMessageId && isDuplicateClientMessage(error)) {
      await assertNotDuplicate(
        {
          sessionId: conversation._id,
          orgId,
          callerId: String(input.userId),
          ownerId: String(conversation.userId),
        },
        body.clientMessageId,
      );
    }
    throw error;
  }
  const notify = notifiableMentions(userRow);
  if (lease && notify.length > 0) {
    void conversationEventProducers()
      .mentioned({
        session: grant.session,
        identity: callerIdentityOf(req),
        actorUserId: String(input.userId),
        messageId: userRow._id.toString(),
        mentions: notify,
      })
      .catch((error: unknown) => {
        logger.warn('chat.mentioned publish failed', {
          sessionId: String(conversation._id),
          error: error instanceof Error ? error.message : String(error),
        });
      });
  }
  return {
    conversation,
    userRow,
    run: {
      lease,
      requestedBy: authorUserId,
      inReplyTo: userRow._id,
      ...(lease &&
        guestAgentKey !== undefined && { respondingAgentKey: guestAgentKey }),
    },
  };
}

/** The people of a chat for the AI backend; none unless the chat is shared and more than one person is in it. */
export async function rosterFor(
  deps: Pick<ConversationTurnDeps, 'users'>,
  conversation: IChatSessionDocument,
  senderId: string,
  history: readonly IMessage[],
  mentionedNow: readonly MentionRef[] = [],
): Promise<TurnRoster | undefined> {
  if (!isCollaborative(conversation)) return undefined;
  return buildTurnRoster({
    mentionedNow: mentionedNow.flatMap((m) =>
      m.type === 'user' ? [m.id] : [],
    ),
    users: deps.users,
    orgId: conversation.orgId.toString(),
    ownerId: String(conversation.userId),
    senderId,
    history,
  });
}

export interface FollowUpContext {
  previousConversations: ReturnType<typeof formatPreviousConversations>;
  project?: IProjectDocument;
  /** Set only for a leased turn (flag on) in a chat other people can write into, with two or more people in it. */
  collaboration?: CollaborationPayload;
  /** Who the question mentions, as roster refs; set with `collaboration`. */
  mentions?: WireMention[];
}

const GUEST_REF_FALLBACK = 'agent:other';
const HANDLE_SLUG = /^[a-z0-9-]{2,40}$/;

/** How each guest agent in the history is named to the model: `agent:<handle>`, never its key or display name. */
export async function guestAgentRefs(
  history: readonly IMessage[],
  identity: ReturnType<typeof callerIdentityOf>,
  profiles: IAgentProfiles | undefined,
): Promise<ReadonlyMap<string, string>> {
  const views = await respondingAgentViews(history, identity, profiles);
  return new Map(
    [...views].map(([key, view]) => [
      key,
      view.handle && HANDLE_SLUG.test(view.handle)
        ? `agent:${view.handle}`
        : GUEST_REF_FALLBACK,
    ]),
  );
}

/** What the AI backend is told about the turn: the rows before the user's message, and the caller's project scope. */
export async function followUpContext(
  req: AuthenticatedUserRequest,
  turn: AppendedTurn,
  deps: ConversationTurnDeps,
): Promise<FollowUpContext> {
  const { conversation, userRow, run } = turn;
  const history = await deps.feed.historyBefore(conversation._id, userRow.seq);
  // With the lease the guard already proved the sender's own project access (D7); without it the owner is the caller.
  const project = run.lease
    ? conversationContextOf(req).project
    : await loadProjectForSession(
        conversation.orgId.toString(),
        (conversation.userId as unknown as Types.ObjectId).toString(),
        conversation.projectId,
      );
  const roster = run.lease
    ? await rosterFor(
        deps,
        conversation,
        String(run.requestedBy),
        history,
        userRow.mentions,
      )
    : undefined;
  const mentions = roster
    ? toWireMentions(userRow.mentions, roster.authors.refs)
    : [];
  const guestRefs = await guestAgentRefs(
    history,
    callerIdentityOf(req),
    deps.agents ?? agentProfiles(),
  );
  return {
    previousConversations: formatPreviousConversations(
      history,
      roster?.authors,
      guestRefs,
    ),
    project,
    ...(roster && { collaboration: roster.collaboration }),
    ...(mentions.length > 0 && { mentions }),
  };
}

/** The request body for the AI backend: with a lease the Node-minted run id replaces the client's and `resume` has been checked; without one neither is trusted. */
export const aiRequestBody = (
  body: FollowUpBody,
  attachments: IChatAttachmentRef[] | undefined,
  run: TurnRun,
): Record<string, unknown> => {
  // The client's mentions are never forwarded as sent; the validated ones travel with PH-10.5.
  const { mentions: _clientMentions, ...rest } = body;
  return {
    ...rest,
    attachments,
    ...(run.lease ? { runId: run.lease.runId } : { resume: undefined }),
  };
};
