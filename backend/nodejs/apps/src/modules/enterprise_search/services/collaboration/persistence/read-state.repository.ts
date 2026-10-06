import { Types } from 'mongoose';
import { ChatSessionReadState } from '../../../schema/chat.session.read-state.schema';

export interface IReadStateRepository {
  /** Monotonic: a lower `seq` than the stored one is a no-op. */
  markRead(
    orgId: string,
    userId: string,
    sessionId: string,
    seq: number,
    opts?: { minIntervalMs?: number },
  ): Promise<void>;
  /** Users among `userIds` whose read state for the session was written at or after `since`. */
  readSince(
    sessionId: string,
    userIds: readonly string[],
    since: Date,
  ): Promise<ReadonlySet<string>>;
  /** One query for a page of sessions; sessions the user never opened are absent. */
  lastReadSeqs(
    userId: string,
    sessionIds: readonly string[],
  ): Promise<ReadonlyMap<string, number>>;
}

const DUPLICATE_KEY = 11000;

export class MongoReadStateRepository implements IReadStateRepository {
  async markRead(
    orgId: string,
    userId: string,
    sessionId: string,
    seq: number,
    opts: { minIntervalMs?: number } = {},
  ): Promise<void> {
    const key = {
      userId: new Types.ObjectId(userId),
      sessionId: new Types.ObjectId(sessionId),
    };
    // With an interval, a row written more recently is not matched; the upsert then collides with it on the unique index, which is the throttle.
    const filter =
      opts.minIntervalMs === undefined
        ? key
        : {
            ...key,
            updatedAt: { $lt: new Date(Date.now() - opts.minIntervalMs) },
          };
    try {
      await ChatSessionReadState.updateOne(
        filter,
        {
          $max: { lastReadSeq: seq },
          $setOnInsert: { orgId: new Types.ObjectId(orgId) },
        },
        { upsert: true },
      );
    } catch (error) {
      if (
        opts.minIntervalMs !== undefined &&
        (error as { code?: number }).code === DUPLICATE_KEY
      ) {
        return;
      }
      throw error;
    }
  }

  async lastReadSeqs(
    userId: string,
    sessionIds: readonly string[],
  ): Promise<ReadonlyMap<string, number>> {
    if (sessionIds.length === 0) {
      return new Map();
    }
    const rows = await ChatSessionReadState.find(
      {
        userId: new Types.ObjectId(userId),
        sessionId: { $in: sessionIds.map((id) => new Types.ObjectId(id)) },
      },
      { sessionId: 1, lastReadSeq: 1 },
    )
      .lean()
      .exec();
    return new Map(rows.map((r) => [r.sessionId.toString(), r.lastReadSeq]));
  }

  async readSince(
    sessionId: string,
    userIds: readonly string[],
    since: Date,
  ): Promise<ReadonlySet<string>> {
    if (userIds.length === 0) {
      return new Set();
    }
    const rows = await ChatSessionReadState.find(
      {
        sessionId: new Types.ObjectId(sessionId),
        userId: { $in: userIds.map((id) => new Types.ObjectId(id)) },
        updatedAt: { $gte: since },
      },
      { userId: 1 },
    )
      .lean()
      .exec();
    return new Set(rows.map((r) => r.userId.toString()));
  }
}
