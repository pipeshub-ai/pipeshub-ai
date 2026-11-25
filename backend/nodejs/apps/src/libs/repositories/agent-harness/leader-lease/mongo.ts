import { LeaderLease } from '../../../../modules/agent-harness/ports/leader-lease';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import { AgentHarnessLeaseModel } from '../execution/schema/execution.schema';
import { MongoTime, mongoServerTime } from '../time/mongo';

export class MongoLeaderLease implements LeaderLease {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  async tryAcquire(
    name: string,
    holder: string,
    ttlMs: number,
  ): Promise<boolean> {
    // Upserts cannot filter on $expr, so the takeover rule (ours, absent, or
    // lapsed on DB time) is evaluated inside the update itself.
    const now = this.time.expr();
    const mayTake = {
      $or: [
        { $eq: ['$holder', { $literal: holder }] },
        { $not: [{ $isNumber: '$expiresAt' }] },
        { $lte: ['$expiresAt', now] },
      ],
    };
    const lease = await AgentHarnessLeaseModel.findOneAndUpdate(
      { _id: name },
      [
        {
          $set: {
            holder: { $cond: [mayTake, { $literal: holder }, '$holder'] },
            expiresAt: {
              $cond: [mayTake, { $add: [now, ttlMs] }, '$expiresAt'],
            },
          },
        },
      ],
      {
        updatePipeline: true,
        upsert: true,
        returnDocument: 'after',
        ...MAJORITY_WRITE,
      },
    ).lean();
    return lease.holder === holder;
  }

  async release(name: string, holder: string): Promise<void> {
    await AgentHarnessLeaseModel.deleteOne({ _id: name, holder });
  }
}
