import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession } from 'mongoose';
import {
  AbandonedFork,
  AbandonedForks,
} from '../../../../modules/agent-harness/ports/session-forks';
import { AgentSessionForkModel } from '../session-forks/schema/session-forks.schema';

/** `agentSessionForks` records left uncommitted (CLEANUP-G4). */
export class MongoAbandonedForks implements AbandonedForks<Tx> {
  async list(before: number, limit: number): Promise<AbandonedFork[]> {
    const records = await AgentSessionForkModel.find(
      { result: null, createdAt: { $lt: before } },
      { orgId: 1, forkSessionId: 1 },
    )
      .sort({ createdAt: 1 })
      .limit(limit)
      // The whole page in the first batch: the server's default first batch is 101.
      .batchSize(limit)
      .read('primary')
      .lean();
    return records.map((record) => ({
      orgId: record.orgId,
      recordId: record._id,
      forkSessionId: record.forkSessionId,
    }));
  }

  async dropMany(
    orgId: string,
    forks: readonly AbandonedFork[],
    before: number,
    txHandle: Tx | ClientSession,
  ): Promise<AbandonedFork[]> {
    const tx = asMongoSession(txHandle);
    const listed = new Map(forks.map((fork) => [fork.recordId, fork]));
    const stillAbandoned = {
      orgId,
      result: null,
      createdAt: { $lt: before },
    };
    // Read and removed in one transaction: a commit landing between the two is a write conflict, so the transaction
    // is retried and the committed fork is read as committed.
    const found = await AgentSessionForkModel.find(
      { _id: { $in: [...listed.keys()] }, ...stillAbandoned },
      { _id: 1, forkSessionId: 1 },
    )
      .limit(listed.size)
      .batchSize(listed.size)
      .session(tx)
      .lean();
    const gone = found.flatMap((record) => {
      const fork = listed.get(record._id);
      return fork !== undefined && fork.forkSessionId === record.forkSessionId
        ? [fork]
        : [];
    });
    if (gone.length === 0) return [];
    await AgentSessionForkModel.deleteMany(
      { _id: { $in: gone.map((fork) => fork.recordId) }, ...stillAbandoned },
      { session: tx },
    );
    return gone;
  }
}
