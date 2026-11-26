import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession, Types } from 'mongoose';
import { principalRefFor } from '../../../../modules/agent-harness/domain/run-spec';
import {
  AwaitingErasure,
  ConversationRetention,
  ErasureRequestCursor,
} from '../../../../modules/agent-harness/ports/session-retention';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';

/**
 * Which conversations an org's retention period has passed, and expiring one
 * the way a delete does (`isDeleted`, `erasureRequestedAt`), in the
 * transaction that enqueues its erasure.
 */
export class HarnessConversationRetention implements ConversationRetention<Tx> {
  async idleSince(
    orgId: string,
    before: number,
    limit: number,
  ): Promise<string[]> {
    if (!Types.ObjectId.isValid(orgId)) return [];
    const sessions = await ChatSession.find(
      {
        orgId: new Types.ObjectId(orgId),
        isDeleted: false,
        lastActivityAt: { $lt: before },
      },
      { _id: 1 },
    )
      .sort({ lastActivityAt: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    return sessions.map((session) => String(session._id));
  }

  async expire(
    orgId: string,
    sessionId: string,
    before: number,
    txHandle: Tx | ClientSession,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    if (!Types.ObjectId.isValid(sessionId) || !Types.ObjectId.isValid(orgId)) {
      return false;
    }
    const expired = await ChatSession.updateOne(
      {
        _id: new Types.ObjectId(sessionId),
        orgId: new Types.ObjectId(orgId),
        isDeleted: false,
        lastActivityAt: { $lt: before },
      },
      { $set: { isDeleted: true, erasureRequestedAt: Date.now() } },
      { session: tx },
    );
    return expired.modifiedCount === 1;
  }

  async awaitingErasure(
    after: ErasureRequestCursor | null,
    limit: number,
  ): Promise<AwaitingErasure[]> {
    const afterFilter =
      after !== null && Types.ObjectId.isValid(after.sessionId)
        ? {
            $or: [
              { erasureRequestedAt: { $gt: after.requestedAt } },
              {
                erasureRequestedAt: after.requestedAt,
                _id: { $gt: new Types.ObjectId(after.sessionId) },
              },
            ],
          }
        : {};
    const sessions = await ChatSession.find(
      {
        // `$exists` keeps the query on the partial `erasure_requested` index.
        erasureRequestedAt: { $exists: true },
        isDeleted: true,
        ...afterFilter,
      },
      { _id: 1, orgId: 1, deletedBy: 1, erasureRequestedAt: 1 },
    )
      .sort({ erasureRequestedAt: 1, _id: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    return sessions.map((session) => ({
      orgId: String(session.orgId),
      sessionId: String(session._id),
      requestedAt: Number(session.erasureRequestedAt),
      by:
        session.deletedBy !== undefined
          ? principalRefFor(String(session.deletedBy))
          : null,
    }));
  }
}
