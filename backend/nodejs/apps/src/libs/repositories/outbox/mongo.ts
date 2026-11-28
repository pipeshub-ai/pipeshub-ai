import type { QueryFilter, Types } from 'mongoose';
import { escapeRegExp } from '../../../utils/escape-regexp';
import { sessionOf } from '../mongo';
import { toPlain } from '../plain';
import type { Tx } from '../uow';
import { eventOrgOf } from './event-org';
import { OutboxEvent, type IOutboxEvent } from './schema/outbox.schema';
import type {
  ClaimOptions,
  NewOutboxEvent,
  OutboxEventRow,
  OutboxRepository,
  OutboxStatus,
} from './types';

/**
 * The ids of the org's events, as the PostgreSQL copy assigns them (`eventOrgOf`): the value is JSON text, so a text match
 * only narrows the candidates and each is then checked on its own top-level org, never on an `orgId` nested in its payload.
 */
async function* orgEventIds(
  orgId: string,
  where: QueryFilter<IOutboxEvent> = {},
): AsyncGenerator<Types.ObjectId> {
  const id = escapeRegExp(orgId);
  const org = orgId.toLowerCase();
  const rows = OutboxEvent.find(
    {
      ...where,
      $or: [
        { value: { $regex: `"orgId":"${id}"` } },
        { orderingKey: { $regex: `^(org|user):${id}(:|$)` } },
      ],
    },
    { _id: 1, value: 1, orderingKey: 1 },
  )
    .lean<Array<{ _id: Types.ObjectId; value: string; orderingKey: string }>>()
    .cursor();
  for await (const row of rows) {
    if (eventOrgOf(row.value, row.orderingKey) === org) yield row._id;
  }
}

const DELETE_BATCH = 500;

export class MongoOutboxRepository implements OutboxRepository {
  async append(event: NewOutboxEvent, tx?: Tx): Promise<void> {
    const session = sessionOf(tx);
    await OutboxEvent.create([event], session ? { session } : {});
  }

  async claimNext(opts: ClaimOptions): Promise<OutboxEventRow | null> {
    const now = new Date();
    const due: QueryFilter<IOutboxEvent> = {
      $or: [
        { status: 'pending', nextAttemptAt: { $lte: now } },
        {
          status: 'publishing',
          claimedAt: { $lte: new Date(now.getTime() - opts.leaseMs) },
        },
      ],
    };

    for (let skip = 0; skip < opts.maxScan; skip += opts.candidateBatch) {
      const candidates = await OutboxEvent.find(due)
        .sort({ createdAt: 1, _id: 1 })
        .skip(skip)
        .limit(opts.candidateBatch)
        .exec();

      if (candidates.length === 0) return null;

      for (const candidate of candidates) {
        // Earlier in the sort's own order: older, or the same instant and a lower _id.
        const blocked = await OutboxEvent.exists({
          orderingKey: candidate.orderingKey,
          status: { $in: ['pending', 'publishing', 'failed'] },
          $or: [
            {
              createdAt: { $lt: candidate.createdAt },
              _id: { $ne: candidate._id },
            },
            { createdAt: candidate.createdAt, _id: { $lt: candidate._id } },
          ],
        });
        if (blocked) continue;

        // The whole due condition is re-asserted here, not just the id and
        // status. Matching on status alone would let two instances take the
        // same abandoned row: the first refreshes claimedAt but leaves the
        // status at `publishing`, so the second update would still match and
        // both would publish the same event.
        const claimed = await OutboxEvent.findOneAndUpdate(
          { _id: candidate._id, ...due },
          { $set: { status: 'publishing', claimedAt: now } },
          { returnDocument: 'after' },
        ).exec();
        if (claimed) return toPlain<OutboxEventRow>(claimed);
      }

      if (candidates.length < opts.candidateBatch) return null;
    }
    return null;
  }

  async markPublished(id: string): Promise<void> {
    await OutboxEvent.updateOne(
      { _id: id },
      {
        $set: { status: 'published', publishedAt: new Date() },
        $unset: { claimedAt: '', lastError: '' },
      },
    ).exec();
  }

  async markFailed(
    id: string,
    failure: { attempts: number; nextAttemptAt: Date; lastError: string },
  ): Promise<void> {
    await OutboxEvent.updateOne(
      { _id: id },
      {
        $set: {
          status: 'pending',
          attempts: failure.attempts,
          nextAttemptAt: failure.nextAttemptAt,
          lastError: failure.lastError,
        },
        $unset: { claimedAt: '' },
      },
    ).exec();
  }

  async eraseDeliveredForOrg(orgId: string, limit: number): Promise<number> {
    if (limit <= 0) return 0;
    const delivered = {
      status: { $in: ['published', 'failed'] as OutboxStatus[] },
    };
    const batch: Types.ObjectId[] = [];
    for await (const id of orgEventIds(orgId, delivered)) {
      batch.push(id);
      if (batch.length >= limit) break;
    }
    if (batch.length === 0) return 0;
    const deleted = await OutboxEvent.deleteMany({
      ...delivered,
      _id: { $in: batch },
    });
    return deleted.deletedCount;
  }

  async deleteAllForOrg(orgId: string): Promise<number> {
    const ids: Types.ObjectId[] = [];
    for await (const id of orgEventIds(orgId)) ids.push(id);
    let deleted = 0;
    for (let i = 0; i < ids.length; i += DELETE_BATCH) {
      deleted += (
        await OutboxEvent.deleteMany({
          _id: { $in: ids.slice(i, i + DELETE_BATCH) },
        })
      ).deletedCount;
    }
    return deleted;
  }

  async countForOrg(orgId: string): Promise<number> {
    let n = 0;
    for await (const _id of orgEventIds(orgId)) n += 1;
    return n;
  }

  async statusCounts(): Promise<Record<OutboxStatus, number>> {
    const rows = await OutboxEvent.aggregate<{ _id: string; count: number }>([
      { $group: { _id: '$status', count: { $sum: 1 } } },
    ]);
    const counts: Record<OutboxStatus, number> = {
      pending: 0,
      publishing: 0,
      published: 0,
      failed: 0,
    };
    for (const row of rows) {
      if (row._id in counts) {
        counts[row._id as OutboxStatus] = row.count;
      }
    }
    return counts;
  }
}
