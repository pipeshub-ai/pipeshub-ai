import mongoose from 'mongoose';
import { AgentCallEvent } from '../../../modules/oauth_provider/schema/agent-call-event.schema';
import { toPlain } from '../plain';
import {
  STATUS_RANGES,
  minuteOf,
  type AgentCallDto,
  type AgentCallFilter,
  type AgentCallRepository,
  type NewAgentCall,
} from './types';

const Types = mongoose.Types;

export class MongoAgentCallRepository implements AgentCallRepository {
  async record(call: NewAgentCall): Promise<void> {
    const { rollup, ...c } = call;
    const doc = {
      ...c,
      orgId: new Types.ObjectId(c.orgId),
      userId: new Types.ObjectId(c.userId),
    };
    if (!rollup) {
      await AgentCallEvent.create({ ...doc, count: 1 });
      return;
    }
    const now = new Date();
    const key = {
      orgId: doc.orgId,
      tokenId: doc.tokenId,
      method: doc.method,
      route: doc.route,
      bucket: minuteOf(now),
    };
    // The key fields are not repeated in $setOnInsert: the upsert writes them from the filter.
    const { orgId: _o, tokenId: _t, method: _m, route: _r, ...rest } = doc;
    await AgentCallEvent.updateOne(
      key,
      { $inc: { count: 1 }, $setOnInsert: { ...rest, createdAt: now } },
      { upsert: true },
    );
  }

  async list(
    orgId: string,
    filter: AgentCallFilter,
    limit: number,
  ): Promise<AgentCallDto[]> {
    const and: Record<string, unknown>[] = [
      { orgId: { $eq: new Types.ObjectId(orgId) } },
    ];
    const created: Record<string, Date> = {};
    if (filter.from) created.$gte = filter.from;
    if (filter.to) created.$lte = filter.to;
    if (created.$gte || created.$lte) and.push({ createdAt: created });
    if (filter.userId)
      and.push({ userId: { $eq: new Types.ObjectId(filter.userId) } });
    if (filter.tokenId) and.push({ tokenId: { $eq: filter.tokenId } });
    if (filter.tokenKind) and.push({ tokenKind: { $eq: filter.tokenKind } });
    if (filter.client) and.push({ userAgent: { $eq: filter.client } });
    if (filter.statusClass) {
      const [lo, hi] = STATUS_RANGES[filter.statusClass];
      and.push({ statusCode: { $gte: lo, $lt: hi } });
    }
    if (filter.before) {
      const { createdAt, id } = filter.before;
      and.push({
        $or: [
          { createdAt: { $lt: createdAt } },
          {
            createdAt: { $eq: createdAt },
            _id: { $lt: new Types.ObjectId(id) },
          },
        ],
      });
    }
    const rows = await AgentCallEvent.find({ $and: and })
      .sort({ createdAt: -1, _id: -1 })
      .limit(limit)
      .lean()
      .exec();
    return rows.map((r) => {
      const {
        bucket: _bucket,
        __v: _v,
        ...rest
      } = toPlain<AgentCallDto & { bucket?: Date; __v?: number }>(r);
      return rest;
    });
  }
}
