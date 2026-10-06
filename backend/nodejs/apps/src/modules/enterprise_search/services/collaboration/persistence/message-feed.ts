import { ClientSession, Types } from 'mongoose';
import { ChatSession } from '../../../schema/chat.session.schema';
import { ChatSessionMessage } from '../../../schema/chat.session.message.schema';
import { IMessage } from '../../../types/conversation.interfaces';

export interface FeedHead {
  rev: number;
  /** The highest `seq` handed out so far (`allocateSeq` returns the end of its block); 0 for an empty conversation. */
  lastSeq: number;
  lastActivityAt: number;
  activeRun: { runId: string; userId: string; startedAt: Date } | null;
}

export interface IConversationMessageFeed {
  /** Rows with `seq` below `seq`, oldest first: the history a turn is answered from. */
  historyBefore(
    sessionId: Types.ObjectId | string,
    seq: number,
    opts?: { dbSession?: ClientSession | null },
  ): Promise<IMessage[]>;
  /** At most `limit` rows with `seq` above `afterSeq`, oldest first, citations populated as the detail route does. */
  listAfter(
    sessionId: Types.ObjectId | string,
    afterSeq: number,
    limit: number,
  ): Promise<IMessage[]>;
  /** One indexed `_id` read projecting only `rev`; null when the conversation is gone. */
  readRev(
    sessionId: Types.ObjectId | string,
    orgId: string,
  ): Promise<number | null>;
  readHead(
    sessionId: Types.ObjectId | string,
    orgId: string,
  ): Promise<FeedHead | null>;
}

interface HeadDoc {
  rev?: number;
  nextSeq?: number;
  lastActivityAt?: number;
  activeRun?: {
    runId: string;
    userId: Types.ObjectId;
    startedAt: Date;
  } | null;
}

export class MongoConversationMessageFeed implements IConversationMessageFeed {
  async historyBefore(
    sessionId: Types.ObjectId | string,
    seq: number,
    opts: { dbSession?: ClientSession | null } = {},
  ): Promise<IMessage[]> {
    let query = ChatSessionMessage.find({ sessionId, seq: { $lt: seq } }).sort({
      seq: 1,
    });
    if (opts.dbSession) {
      query = query.session(opts.dbSession);
    }
    return (await query.lean().exec()) as unknown as IMessage[];
  }

  async listAfter(
    sessionId: Types.ObjectId | string,
    afterSeq: number,
    limit: number,
  ): Promise<IMessage[]> {
    return (await ChatSessionMessage.find({ sessionId, seq: { $gt: afterSeq } })
      .sort({ seq: 1 })
      .limit(limit)
      .populate({
        path: 'citations.citationId',
        model: 'citation',
        select: '-__v',
      })
      .lean()
      .exec()) as unknown as IMessage[];
  }

  async readRev(
    sessionId: Types.ObjectId | string,
    orgId: string,
  ): Promise<number | null> {
    const doc = await ChatSession.findOne(
      { _id: sessionId, orgId: new Types.ObjectId(orgId), isDeleted: false },
      { rev: 1 },
    )
      .lean<HeadDoc>()
      .exec();
    return doc ? (doc.rev ?? 0) : null;
  }

  async readHead(
    sessionId: Types.ObjectId | string,
    orgId: string,
  ): Promise<FeedHead | null> {
    const doc = await ChatSession.findOne({
      _id: sessionId,
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
    })
      .select('rev lastActivityAt activeRun +nextSeq')
      .lean<HeadDoc>()
      .exec();
    if (!doc) {
      return null;
    }
    return {
      rev: doc.rev ?? 0,
      lastSeq: doc.nextSeq ?? 0,
      lastActivityAt: doc.lastActivityAt ?? 0,
      activeRun: doc.activeRun
        ? {
            runId: doc.activeRun.runId,
            userId: doc.activeRun.userId.toString(),
            startedAt: doc.activeRun.startedAt,
          }
        : null,
    };
  }
}
