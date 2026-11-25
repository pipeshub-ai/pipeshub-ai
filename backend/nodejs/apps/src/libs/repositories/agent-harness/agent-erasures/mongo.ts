import {
  AgentErasureQueue,
  PendingAgentErasure,
} from '../../../../modules/agent-harness/ports/agent-erasures';
import {
  AgentErasureDocument,
  AgentErasureModel,
} from './schema/agent-erasures.schema';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import { MongoTime, mongoServerTime } from '../time/mongo';

const id = (orgId: string, agentKey: string): string => `${orgId}:${agentKey}`;

const toEntry = (document: AgentErasureDocument): PendingAgentErasure => ({
  orgId: document.orgId,
  agentKey: document.agentKey,
  confirmed: document.confirmed ?? true,
  failures: document.failures,
  requestedAt: document.requestedAt,
  nextAttemptAt: document.nextAttemptAt,
  lastErrorType: document.lastErrorType ?? null,
});

/** In a pipeline update: the document did not exist before this statement. */
const FRESH = { $eq: [{ $type: '$requestedAt' }, 'missing'] };
/** A missing `confirmed` is an H4 entry, written after the delete. */
const CONFIRMED = { $ne: ['$confirmed', false] };

/** Pending agent erasures in `agentErasures`, due on DB time. */
export class MongoAgentErasureQueue implements AgentErasureQueue {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  async intend(
    orgId: string,
    agentKey: string,
    afterMs: number,
  ): Promise<void> {
    const now = this.time.expr();
    const requested = { $or: [FRESH, { $not: [CONFIRMED] }] };
    await AgentErasureModel.updateOne(
      { _id: id(orgId, agentKey) },
      [
        {
          $set: {
            orgId: { $literal: orgId },
            agentKey: { $literal: agentKey },
            confirmed: { $cond: [FRESH, false, '$confirmed'] },
            failures: { $cond: [FRESH, 0, '$failures'] },
            requestedAt: { $cond: [requested, now, '$requestedAt'] },
            nextAttemptAt: {
              $cond: [requested, { $add: [now, afterMs] }, '$nextAttemptAt'],
            },
            lastErrorType: { $cond: [FRESH, null, '$lastErrorType'] },
          },
        },
      ],
      { updatePipeline: true, upsert: true, ...MAJORITY_WRITE },
    );
  }

  async enqueue(
    orgId: string,
    agentKey: string,
    errorType: string,
  ): Promise<void> {
    const now = this.time.expr();
    const keep = { $and: [{ $not: [FRESH] }, CONFIRMED] };
    await AgentErasureModel.updateOne(
      { _id: id(orgId, agentKey) },
      [
        {
          $set: {
            orgId: { $literal: orgId },
            agentKey: { $literal: agentKey },
            failures: {
              $cond: [
                keep,
                '$failures',
                { $add: [{ $ifNull: ['$failures', 0] }, 1] },
              ],
            },
            requestedAt: { $ifNull: ['$requestedAt', now] },
            nextAttemptAt: { $cond: [keep, '$nextAttemptAt', now] },
            lastErrorType: {
              $cond: [keep, '$lastErrorType', { $literal: errorType }],
            },
          },
        },
        // After the fields above read the old `confirmed`: set it last.
        { $set: { confirmed: true } },
      ],
      { updatePipeline: true, upsert: true, ...MAJORITY_WRITE },
    );
  }

  async due(now: number, limit: number): Promise<PendingAgentErasure[]> {
    const documents = await AgentErasureModel.find({
      nextAttemptAt: { $lte: now },
    })
      .sort({ nextAttemptAt: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    return documents.map(toEntry);
  }

  async retryLater(
    entry: PendingAgentErasure,
    at: number,
    errorType: string,
  ): Promise<void> {
    await AgentErasureModel.updateOne(
      { _id: id(entry.orgId, entry.agentKey) },
      {
        $set: { nextAttemptAt: at, lastErrorType: errorType },
        $inc: { failures: 1 },
      },
      MAJORITY_WRITE,
    );
  }

  async postpone(entry: PendingAgentErasure, at: number): Promise<void> {
    await AgentErasureModel.updateOne(
      { _id: id(entry.orgId, entry.agentKey) },
      { $set: { nextAttemptAt: at } },
      MAJORITY_WRITE,
    );
  }

  async settle(orgId: string, agentKey: string): Promise<void> {
    await AgentErasureModel.deleteOne(
      { _id: id(orgId, agentKey) },
      MAJORITY_WRITE,
    );
  }

  async withdraw(entry: PendingAgentErasure): Promise<boolean> {
    const result = await AgentErasureModel.deleteOne(
      {
        _id: id(entry.orgId, entry.agentKey),
        confirmed: false,
        requestedAt: entry.requestedAt,
      },
      MAJORITY_WRITE,
    );
    return result.deletedCount === 1;
  }
}
