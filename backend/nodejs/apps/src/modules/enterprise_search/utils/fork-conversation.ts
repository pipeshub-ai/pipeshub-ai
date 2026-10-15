import mongoose, { ClientSession } from 'mongoose';
import {
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { ProjectService } from '../../projects/services/project.service';
import { CONVERSATION_STATUS } from '../constants/constants';
import { ChatSession } from '../schema/chat.session.schema';
import { ChatSessionMessage } from '../schema/chat.session.message.schema';
import {
  IChatSession,
  IChatSessionDocument,
  IMessage,
  IMessagePart,
} from '../types/conversation.interfaces';
import { appendMessages } from './utils';

export const FORK_TITLE_MAX_LENGTH = 200;
export const FORK_TITLE_PREFIX = 'Fork of ';
export const FORK_COPY_BATCH_SIZE = 200;

const MESSAGE_COPY_OMIT = new Set([
  '_id',
  '__v',
  'id',
  'sessionId',
  'orgId',
  'seq',
  'createdAt',
  'updatedAt',
  'feedback',
]);

export function buildForkTitle(originalTitle?: string | null): string {
  const base = (originalTitle ?? '').trim() || 'Untitled';
  const titled = `${FORK_TITLE_PREFIX}${base}`;
  return titled.length <= FORK_TITLE_MAX_LENGTH
    ? titled
    : titled.slice(0, FORK_TITLE_MAX_LENGTH);
}

export function stripMessageForCopy(
  message: Record<string, unknown>,
): IMessage {
  const copy: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(message)) {
    if (!MESSAGE_COPY_OMIT.has(key)) {
      copy[key] = value;
    }
  }
  return copy as unknown as IMessage;
}

const EXPIRED_APPROVAL_PREVIEW = 'The approval request expired.';

/**
 * A tool call still waiting for approval in the original can only be answered
 * there: Python ties the pending approval to its conversation and ignores an
 * answer sent from another one. In the copy it is settled as expired, the way
 * the original would settle it once the window ran out, so the fork shows a
 * receipt instead of a card that does nothing. Parts are never mutated.
 */
export function expirePendingApprovals(
  parts: IMessagePart[] | undefined,
): IMessagePart[] | undefined {
  if (!Array.isArray(parts)) {
    return parts;
  }
  const settled = parts.map((part) => {
    // Persisted parts are Mixed: an element is not guaranteed to be an object.
    const candidate: unknown = part;
    if (typeof candidate !== 'object' || candidate === null) {
      return part;
    }
    const nested = expirePendingApprovals(part.parts);
    const pending =
      part.type === 'tool_call' &&
      part.status === 'awaiting_approval' &&
      !part.approvalAnswer;
    if (!pending && nested === part.parts) {
      return part;
    }
    return {
      ...part,
      ...(pending && {
        status: 'blocked' as const,
        approvalAnswer: 'expired' as const,
        resultPreview: part.resultPreview ?? EXPIRED_APPROVAL_PREVIEW,
      }),
      ...(nested && { parts: nested }),
    };
  });
  return settled.every((part, i) => part === parts[i]) ? parts : settled;
}

function withExpiredApprovals(message: IMessage): IMessage {
  return Array.isArray(message.parts)
    ? { ...message, parts: expirePendingApprovals(message.parts) }
    : message;
}

export interface ForkSessionFields {
  sessionType: 'chat' | 'agent';
  agentKey?: string;
  /** Set only after the caller was confirmed to still have access to it. */
  projectId?: mongoose.Types.ObjectId;
}

export interface ForkChatSessionResult {
  conversation: IChatSessionDocument;
  messageCount: number;
}

/**
 * The source's project, or undefined when the caller can no longer see it: a
 * fork must not put a new chat into a project the caller lost access to.
 */
export async function resolveForkProjectId(
  source: IChatSessionDocument,
  orgId: string,
  userId: string,
): Promise<mongoose.Types.ObjectId | undefined> {
  if (!source.projectId) {
    return undefined;
  }
  try {
    await ProjectService.assertAccess(
      orgId,
      userId,
      String(source.projectId),
      'viewer',
    );
    return source.projectId;
  } catch (error) {
    if (error instanceof NotFoundError || error instanceof ForbiddenError) {
      return undefined;
    }
    throw error;
  }
}

/**
 * Copy a chat session and every message up to `messageId` (inclusive) into a
 * new session owned by `userId`. The source is left unchanged. Share state is
 * not copied — the fork is private to the caller. Messages are copied in
 * batches so a long thread is never held in memory whole. Without a
 * transaction, a failed copy removes what it already wrote.
 */
export async function copySessionUpToMessage(
  source: IChatSessionDocument,
  messageId: string,
  userId: string,
  orgId: string,
  fields: ForkSessionFields,
  mongoSession?: ClientSession | null,
): Promise<ForkChatSessionResult> {
  const sourceId = source._id;
  const sessionOption = mongoSession ? { session: mongoSession } : undefined;
  const forkPoint = await ChatSessionMessage.findOne(
    { _id: messageId, sessionId: sourceId },
    undefined,
    sessionOption,
  ).lean();

  if (!forkPoint) {
    throw new NotFoundError('Message not found');
  }

  const sessionFields: Partial<IChatSession> = {
    orgId: new mongoose.Types.ObjectId(orgId),
    userId: new mongoose.Types.ObjectId(userId),
    initiator: new mongoose.Types.ObjectId(userId),
    title: buildForkTitle(source.title),
    lastActivityAt: Date.now(),
    status: CONVERSATION_STATUS.COMPLETE,
    modelInfo: source.modelInfo,
    sessionType: fields.sessionType,
    isShared: false,
    isDeleted: false,
    isArchived: false,
    metadata: new Map<string, unknown>([
      ['forkedFromConversationId', String(sourceId)],
      ['forkedFromMessageId', messageId],
    ]),
  };

  if (fields.sessionType === 'agent') {
    sessionFields.agentKey = fields.agentKey ?? source.agentKey;
    sessionFields.conversationSource = 'agent_chat';
  }

  if (fields.projectId) {
    sessionFields.projectId = fields.projectId;
    sessionFields.projectVisibility = 'private';
  }

  const conversation = (await new ChatSession(sessionFields).save(
    sessionOption,
  )) as IChatSessionDocument;
  const forkId = conversation._id;

  let messageCount = 0;
  try {
    let nextSeq = 0;
    for (;;) {
      const batch = await ChatSessionMessage.find(
        { sessionId: sourceId, seq: { $gte: nextSeq, $lte: forkPoint.seq } },
        undefined,
        sessionOption,
      )
        .sort({ seq: 1 })
        .limit(FORK_COPY_BATCH_SIZE)
        .lean();
      if (batch.length === 0) {
        break;
      }
      await appendMessages(
        forkId,
        conversation.orgId,
        batch.map((row) =>
          withExpiredApprovals(
            stripMessageForCopy(row as Record<string, unknown>),
          ),
        ),
        mongoSession,
      );
      messageCount += batch.length;
      nextSeq = (batch[batch.length - 1] as { seq: number }).seq + 1;
    }
  } catch (error) {
    if (!mongoSession) {
      await ChatSessionMessage.deleteMany({ sessionId: forkId });
      await ChatSession.deleteOne({ _id: forkId });
    }
    throw error;
  }

  return { conversation, messageCount };
}
