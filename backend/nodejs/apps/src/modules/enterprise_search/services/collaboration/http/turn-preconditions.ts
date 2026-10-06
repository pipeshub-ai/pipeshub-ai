import { Types } from 'mongoose';
import { ChatSession } from '../../../schema/chat.session.schema';
import { ChatSessionMessage } from '../../../schema/chat.session.message.schema';
import {
  ConversationChangedError,
  DuplicateMessageError,
} from '../domain/errors';

export interface TurnScope {
  readonly sessionId: Types.ObjectId;
  readonly orgId: Types.ObjectId;
  readonly callerId: string;
  /** Authors legacy rows without `authorUserId`/`requestedBy`. */
  readonly ownerId: string;
}

/**
 * 409 when rows newer than the caller's `baseSeq` were written by someone else. Legacy rows read
 * as the owner's. The caller's own newer rows are not a conflict.
 */
export async function assertNotChangedSince(
  scope: TurnScope,
  baseSeq: number,
): Promise<void> {
  const session = await ChatSession.findOne({
    _id: scope.sessionId,
    orgId: scope.orgId,
  })
    .select('+nextSeq')
    .lean<{ nextSeq?: number }>();
  // `nextSeq` is the highest seq handed out (allocateSeq returns the end of its block), not the next free one.
  if ((session?.nextSeq ?? 0) <= baseSeq) {
    return;
  }
  const caller = new Types.ObjectId(scope.callerId);
  const foreign: Record<string, unknown>[] = [
    { authorUserId: { $nin: [null, caller] } },
    { authorUserId: null, requestedBy: { $nin: [null, caller] } },
  ];
  if (scope.ownerId !== scope.callerId) {
    foreign.push({ authorUserId: null, requestedBy: null });
  }
  const newerCount = await ChatSessionMessage.countDocuments({
    sessionId: scope.sessionId,
    orgId: scope.orgId,
    seq: { $gt: baseSeq },
    $or: foreign,
  });
  if (newerCount > 0) {
    throw new ConversationChangedError(newerCount);
  }
}

const isAnswered = async (
  sessionId: Types.ObjectId,
  orgId: Types.ObjectId,
  messageId: Types.ObjectId,
): Promise<boolean> =>
  (await ChatSessionMessage.countDocuments({
    sessionId,
    orgId,
    messageType: 'bot_response',
    inReplyTo: messageId,
  })) > 0;

/** 409 when this caller already sent `clientMessageId` in this conversation. */
export async function assertNotDuplicate(
  scope: TurnScope,
  clientMessageId: string,
): Promise<void> {
  const original = await ChatSessionMessage.findOne({
    sessionId: scope.sessionId,
    orgId: scope.orgId,
    authorUserId: new Types.ObjectId(scope.callerId),
    clientMessageId,
  })
    .select('_id')
    .lean<{ _id: Types.ObjectId }>();
  if (!original) {
    return;
  }
  throw new DuplicateMessageError(
    original._id.toString(),
    await isAnswered(scope.sessionId, scope.orgId, original._id),
  );
}

/** True for a unique-key violation (11000) on the `{orgId, initiator, creationKey}` index. */
export const isDuplicateCreationKey = (error: unknown): boolean => {
  const e = error as {
    code?: unknown;
    keyPattern?: Record<string, unknown>;
    message?: unknown;
  } | null;
  return (
    e?.code === 11000 &&
    (e.keyPattern?.creationKey !== undefined ||
      /creationKey/.test(String(e.message)))
  );
};

/**
 * 409 when this caller already started a conversation with `creationKey` (the first send's
 * `clientMessageId`); the retry learns the conversation it created.
 */
export async function assertNotDuplicateFirstSend(
  orgId: Types.ObjectId,
  callerId: string,
  creationKey: string,
): Promise<void> {
  const caller = new Types.ObjectId(callerId);
  const existing = await ChatSession.findOne({
    orgId,
    initiator: caller,
    creationKey,
  })
    .select('+creationKey')
    .lean<{ _id: Types.ObjectId }>();
  if (!existing) {
    return;
  }
  const original = await ChatSessionMessage.findOne({
    sessionId: existing._id,
    orgId,
    authorUserId: caller,
    clientMessageId: creationKey,
  })
    .select('_id')
    .lean<{ _id: Types.ObjectId }>();
  throw new DuplicateMessageError(
    original?._id.toString() ?? null,
    original ? await isAnswered(existing._id, orgId, original._id) : false,
    existing._id.toString(),
  );
}
