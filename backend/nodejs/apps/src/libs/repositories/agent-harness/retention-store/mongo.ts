import { TERMINAL_RUN_STATUSES } from '../../../../modules/agent-harness/domain/execution';
import { RetentionStore } from '../../../../modules/agent-harness/ports/session-retention';
import { AgentHarnessDataKeyModel } from '../data-protection/schema/data-protection.schema';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import {
  AgentAdmissionReceiptModel,
  AgentCommandReceiptModel,
  AgentRunModel,
} from '../execution/schema/execution.schema';
import { AgentJournalPayloadModel } from '../journal/schema/journal.schema';
import { MongoTime, mongoServerTime } from '../time/mongo';

const TERMINAL = [...TERMINAL_RUN_STATUSES];

/**
 * T5 on Mongo, reference-aware (I8): nothing a run that can still write may
 * read is collected. A run is either terminal, which can append nothing more,
 * or absent, which only an aborted admission leaves.
 */
export class MongoRetentionStore implements RetentionStore {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  now(): Promise<number> {
    return this.time.read();
  }

  async collectPayloads(before: number, limit: number): Promise<number> {
    const candidates = await AgentJournalPayloadModel.find(
      { referenced: false, createdAt: { $lt: new Date(before) } },
      { _id: 1, orgId: 1, owner: 1 },
    )
      .sort({ createdAt: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    if (candidates.length === 0) return 0;
    const live = await this.liveRuns(
      candidates.flatMap((payload) =>
        payload.owner?.runId !== undefined
          ? [{ orgId: payload.orgId, runId: payload.owner.runId }]
          : [],
      ),
    );
    const collectable = candidates
      .filter(
        (payload) =>
          payload.owner?.runId === undefined ||
          !live.has(`${payload.orgId}:${payload.owner.runId}`),
      )
      .map((payload) => payload._id);
    if (collectable.length === 0) return 0;
    // `referenced: false` again: an append that marked one meanwhile keeps it.
    const deleted = await AgentJournalPayloadModel.deleteMany(
      { _id: { $in: collectable }, referenced: false },
      MAJORITY_WRITE,
    );
    return deleted.deletedCount;
  }

  async expireCommandReceipts(before: number, limit: number): Promise<number> {
    const receipts = await AgentCommandReceiptModel.find(
      { createdAt: { $lt: before } },
      { _id: 1, orgId: 1, runId: 1 },
    )
      .sort({ createdAt: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    if (receipts.length === 0) return 0;
    const live = await this.liveRuns(receipts);
    const expired = receipts
      .filter((receipt) => !live.has(`${receipt.orgId}:${receipt.runId}`))
      .map((receipt) => receipt._id);
    if (expired.length === 0) return 0;
    const deleted = await AgentCommandReceiptModel.deleteMany(
      { _id: { $in: expired } },
      MAJORITY_WRITE,
    );
    return deleted.deletedCount;
  }

  async expireAdmissionReceipts(
    before: number,
    limit: number,
  ): Promise<number> {
    const receipts = await AgentAdmissionReceiptModel.find(
      { createdAt: { $lt: before } },
      { _id: 1 },
    )
      .sort({ createdAt: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    if (receipts.length === 0) return 0;
    const deleted = await AgentAdmissionReceiptModel.deleteMany(
      { _id: { $in: receipts.map((receipt) => receipt._id) } },
      MAJORITY_WRITE,
    );
    return deleted.deletedCount;
  }

  async harnessOrgs(): Promise<string[]> {
    return await AgentHarnessDataKeyModel.distinct('orgId');
  }

  /** `orgId:runId` of the runs among `runs` that exist and have not ended. */
  private async liveRuns(
    runs: ReadonlyArray<{ orgId: string; runId: string }>,
  ): Promise<Set<string>> {
    if (runs.length === 0) return new Set();
    const found = await AgentRunModel.find(
      {
        $or: runs.map(({ orgId, runId }) => ({ orgId, runId })),
        status: { $nin: TERMINAL },
      },
      { _id: 0, orgId: 1, runId: 1 },
    )
      .read('primary')
      .lean();
    return new Set(found.map((run) => `${run.orgId}:${run.runId}`));
  }
}
