import mongoose, { Types } from 'mongoose';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { CallerIdentity } from '../../../libs/types/caller-identity';
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
import { titleFromQuery } from '../services/collaboration/mentions/mention.parser';
import { DEFAULT_RESPOND_MODE } from '../services/collaboration/mentions/mention.types';
import { buildNoteMessage } from '../services/collaboration/mentions/note.service';
import { classifyResponder } from '../services/collaboration/mentions/responder-router';
import { MentionRef } from '../services/collaboration/mentions/mention.types';
import { ChatTarget } from './ai-chat-payload';
import { AppendedTurn, FollowUpBody } from './follow-up-turn';
import { openConversation } from './non-streaming-chat';
import { ResolvedProjectLink } from './project-context';
import { mentionLabels } from './mention-labels';
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
  /** The caller, to name the mentions in the title; absent, their tokens are just dropped from it. */
  identity?: CallerIdentity;
}

const sessionFields = (
  input: FirstTurnInput,
  authorUserId: Types.ObjectId,
  orgId: Types.ObjectId,
  labels: ReadonlyMap<string, string>,
  asNote: boolean,
): Partial<IChatSession> => {
  const { target, body, link } = input;
  return {
    orgId,
    userId: authorUserId,
    initiator: authorUserId,
    title: titleFromQuery(body.query, labels),
    lastActivityAt: Date.now(),
    status: asNote
      ? CONVERSATION_STATUS.COMPLETE
      : CONVERSATION_STATUS.INPROGRESS,
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

/** A first send that mentions only people or teams asks nobody: with the mentions on it is stored as a note and no AI run starts. */
export function isNoteFirstSend(
  input: Pick<FirstTurnInput, 'collab' | 'target' | 'mentions'>,
): boolean {
  return (
    input.collab &&
    classifyResponder({
      mentions: input.mentions ?? [],
      respondMode: DEFAULT_RESPOND_MODE,
      sessionKind: input.target.kind === 'agent' ? 'agent' : 'chat',
    }) === 'note'
  );
}

export type FirstTurn = AppendedTurn & { asNote: boolean };

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
): Promise<FirstTurn> {
  const { deps, collab, body } = input;
  const asNote = isNoteFirstSend(input);
  if (asNote && input.attachments && input.attachments.length > 0) {
    throw new BadRequestError(
      'A note cannot carry attachments. Mention @assistant to ask about a file.',
    );
  }
  const authorUserId = new Types.ObjectId(String(input.userId));
  const orgId = new Types.ObjectId(String(input.orgId));
  const fresh =
    collab && !asNote
      ? deps.leases.forNewSession(authorUserId.toString(), orgId.toString())
      : undefined;
  const creationKey = collab ? body.clientMessageId : undefined;
  const userMessage = asNote
    ? buildNoteMessage({
        query: body.query,
        authorUserId,
        clientMessageId: body.clientMessageId,
        mentions: input.mentions ?? [],
        now: new Date(),
      })
    : buildUserQueryMessage(
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

  const labels = await mentionLabels(
    deps,
    input.identity,
    input.mentions ?? [],
  );
  let created: Awaited<ReturnType<typeof openConversation>>;
  try {
    created = await openConversation(
      {
        ...sessionFields(input, authorUserId, orgId, labels, asNote),
        ...(fresh && { activeRun: fresh.activeRun }),
        ...(creationKey && { creationKey }),
      },
      userMessage,
    );
  } catch (error) {
    if (creationKey) {
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
          fresh?.activeRun.runId,
        );
      }
    }
    throw error;
  }

  const { conversation, userRow } = created;
  return {
    asNote,
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
  runId: string | undefined,
): Promise<void> {
  try {
    await ChatSession.deleteOne({
      orgId,
      initiator,
      creationKey: { $eq: creationKey },
      ...(runId !== undefined && { 'activeRun.runId': { $eq: runId } }),
    });
  } catch (error) {
    logger.error('Failed to remove a half-created conversation', {
      error: error instanceof Error ? error.message : String(error),
    });
  }
}
