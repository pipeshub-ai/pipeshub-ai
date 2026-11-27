import { pendingReapId } from '../../../../modules/agent-harness/domain/pending-reaps';
import {
  PendingReap,
  PendingReaps,
} from '../../../../modules/agent-harness/ports/sandbox-reaper';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import { mongoServerTime, MongoTime } from '../time/mongo';
import { AgentSandboxReapModel } from './schema/pending-reaps.schema';

/** `agentSandboxReaps`: refused reaps waiting for the broker (CLEANUP-G6). */
export class MongoPendingReaps implements PendingReaps {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  now(): Promise<number> {
    return this.time.read();
  }

  async defer(
    labels: Readonly<Record<string, string>>,
    at: number,
    errorType: string,
  ): Promise<void> {
    await AgentSandboxReapModel.updateOne(
      { _id: pendingReapId(labels) },
      {
        $setOnInsert: {
          orgId: labels['orgId'] ?? '',
          labels: { ...labels },
          failures: 1,
          nextAttemptAt: at,
          createdAt: await this.time.read(),
          lastErrorType: errorType,
        },
      },
      { upsert: true, ...MAJORITY_WRITE },
    );
  }

  async due(now: number, limit: number): Promise<PendingReap[]> {
    const documents = await AgentSandboxReapModel.find({
      nextAttemptAt: { $lte: now },
    })
      .sort({ nextAttemptAt: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    return documents.map((document) => ({
      id: document._id,
      labels: document.labels,
      failures: document.failures,
    }));
  }

  async done(id: string): Promise<void> {
    await AgentSandboxReapModel.deleteOne({ _id: id }, MAJORITY_WRITE);
  }

  async later(id: string, at: number, errorType: string): Promise<void> {
    await AgentSandboxReapModel.updateOne(
      { _id: id },
      {
        $set: { nextAttemptAt: at, lastErrorType: errorType },
        $inc: { failures: 1 },
      },
      MAJORITY_WRITE,
    );
  }
}
