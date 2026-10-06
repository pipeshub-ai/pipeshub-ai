import mongoose, { Types } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import { CONVERSATION_STATUS } from '../constants/constants';
import { ChatSession } from '../schema/chat.session.schema';
import { ResumeNotAllowedError } from '../services/collaboration/domain/errors';
import {
  assertNotDuplicateFirstSend,
  isDuplicateCreationKey,
} from '../services/collaboration/http/turn-preconditions';
import { ConversationTurnDeps } from '../services/collaboration/turn/turn-deps';
import {
  IChatAttachmentRef,
  IChatSession,
} from '../types/conversation.interfaces';
import { MentionRef } from '../services/collaboration/mentions/mention.types';
import { ChatTarget } from './ai-chat-payload';
import { AppendedTurn, FollowUpBody } from './follow-up-turn';
import { openConversation } from './non-streaming-chat';
import { ResolvedProjectLink } from './project-context';
import { buildUserQueryMessage, extractModelInfo } from './utils';

const logger = Logger.getInstance({ service: 'First turn' });

export interface FirstTurnInput {
  deps: ConversationTurnDeps;
  /** The collaborative-chats flag as the route guard read it. */
  collab: boolean;
  target: ChatTarget;
  userId: Types.ObjectId | string;
  orgId: Types.ObjectId | string;
  body: FollowUpBody;
  attachments?: IChatAttachmentRef[];
  link: ResolvedProjectLink;
  /** The agent a mention handed this turn to, when it is not the chat's own. */
  guestAgentKey?: string;
  /** The validated mentions of the first message; stored with it, with the flags on. */
  mentions?: readonly MentionRef[];
}

const sessionFields = (
  input: FirstTurnInput,
  authorUserId: Types.ObjectId,
  orgId: Types.ObjectId,
): Partial<IChatSession> => {
  const { target, body, link } = input;
  return {
    orgId,
    userId: authorUserId,
    initiator: authorUserId,
    title: body.query.slice(0, 100),
    lastActivityAt: Date.now(),
    status: CONVERSATION_STATUS.INPROGRESS,
    modelInfo: extractModelInfo(body),
    ...(target.kind === 'agent'
      ? {
          agentKey: target.agentKey,
          sessionType: 'agent',
          // The unified chatSessions schema cannot default this: it also backs plain chats.
          conversationSource: 'agent_chat',
        }
      : { sessionType: 'chat' }),
    ...(link.projectId
      ? {
          projectId: new mongoose.Types.ObjectId(link.projectId),
          projectVisibility: link.projectVisibility,
        }
      : {}),
  };
};

/** With the flag on, a first send names no card to answer, so a `resume` on it is refused: 403, before anything is written or any header is sent. */
export function rejectResumeOnFirstSend(
  collab: boolean,
  resume: unknown,
): void {
  if (collab && resume !== undefined) {
    throw new ResumeNotAllowedError();
  }
}

/**
 * With the flag on, 409 when this caller already started a conversation with the same
 * `clientMessageId`. Runs before anything is written or any header is sent.
 */
export async function rejectRepeatedFirstSend(
  collab: boolean,
  orgId: Types.ObjectId | string,
  userId: Types.ObjectId | string,
  clientMessageId: string | undefined,
): Promise<void> {
  if (collab && clientMessageId) {
    await assertNotDuplicateFirstSend(
      new Types.ObjectId(String(orgId)),
      String(userId),
      clientMessageId,
    );
  }
}

/**
 * Creates the conversation and the caller's first `user_query` together. With the flag on the
 * session is born holding the run's lease and carries `creationKey`, so a concurrent send of the
 * same `clientMessageId` that slipped past the pre-check lands here as a unique violation and is
 * rethrown as `DuplicateMessageError` naming the winner's conversation.
 */
export async function createFirstTurn(
  input: FirstTurnInput,
): Promise<AppendedTurn> {
  const { deps, collab, body } = input;
  const authorUserId = new Types.ObjectId(String(input.userId));
  const orgId = new Types.ObjectId(String(input.orgId));
  const fresh = collab
    ? deps.leases.forNewSession(authorUserId.toString(), orgId.toString())
    : undefined;
  const creationKey = fresh ? body.clientMessageId : undefined;
  const userMessage = buildUserQueryMessage(
    body.query,
    body.appliedFilters,
    body.chatMode,
    input.attachments,
    {
      authorUserId,
      ...(fresh && {
        clientMessageId: body.clientMessageId,
        filesShared: body.filesShared,
        shareToolResults: body.shareToolResults,
        runId: fresh.activeRun.runId,
        ...(input.mentions &&
          input.mentions.length > 0 && { mentions: [...input.mentions] }),
      }),
    },
  );

  let created: Awaited<ReturnType<typeof openConversation>>;
  try {
    created = await openConversation(
      {
        ...sessionFields(input, authorUserId, orgId),
        ...(fresh && { activeRun: fresh.activeRun }),
        ...(creationKey && { creationKey }),
      },
      userMessage,
    );
  } catch (error) {
    if (creationKey && fresh) {
      if (isDuplicateCreationKey(error)) {
        await assertNotDuplicateFirstSend(
          orgId,
          authorUserId.toString(),
          creationKey,
        );
      } else {
        await discardOrphan(
          orgId,
          authorUserId,
          creationKey,
          fresh.activeRun.runId,
        );
      }
    }
    throw error;
  }

  const { conversation, userRow } = created;
  return {
    conversation,
    userRow,
    run: {
      lease: fresh?.bind(String(conversation._id)),
      requestedBy: authorUserId,
      inReplyTo: userRow._id,
      ...(fresh &&
        input.guestAgentKey !== undefined && {
          respondingAgentKey: input.guestAgentKey,
        }),
    },
  };
}

/**
 * Without a transaction the session can be saved before its first row fails. Left behind, it would
 * answer the user's retry of the same `clientMessageId` with a 409 for a conversation that has no
 * question in it, so this run's own half-created session is removed.
 */
async function discardOrphan(
  orgId: Types.ObjectId,
  initiator: Types.ObjectId,
  creationKey: string,
  runId: string,
): Promise<void> {
  try {
    await ChatSession.deleteOne({
      orgId,
      initiator,
      creationKey: { $eq: creationKey },
      'activeRun.runId': { $eq: runId },
    });
  } catch (error) {
    logger.error('Failed to remove a half-created conversation', {
      error: error instanceof Error ? error.message : String(error),
    });
  }
}
