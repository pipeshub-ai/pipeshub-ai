import type { ConfigChanged } from '../../../../modules/control-plane/domain/config-change';
import type {
  AcquiredLease,
  OrgHeadLeases,
} from '../../../../modules/control-plane/adapters/ordered-steps-transaction';
import { CpOrgHeadModel } from './schema/org-heads.schema';

const DUPLICATE_KEY = 11000;

const isDuplicateKey = (error: unknown): boolean =>
  typeof error === 'object' &&
  error !== null &&
  (error as { code?: unknown }).code === DUPLICATE_KEY;

/** `cpOrgHeads` with a lease per org, for the ordered-steps writer (no transactions). */
export class MongoOrgHeadLeases implements OrgHeadLeases {
  async acquire(
    orgId: string,
    token: string,
    now: number,
    until: number,
  ): Promise<AcquiredLease | null> {
    try {
      // A live lease fails the filter, and the upsert then collides on `_id`: busy.
      const before = await CpOrgHeadModel.findOneAndUpdate(
        {
          _id: orgId,
          $or: [{ lease: null }, { 'lease.until': { $lte: now } }],
        },
        {
          $set: {
            lease: { token, until, intent: null },
            updatedAt: new Date(now),
          },
          $setOnInsert: { revision: 0 },
        },
        { upsert: true, new: false },
      ).lean();
      return {
        revision: before?.revision ?? 0,
        stale: before?.lease?.intent ?? null,
      };
    } catch (error) {
      if (isDuplicateKey(error)) return null;
      throw error;
    }
  }

  async extend(orgId: string, token: string, until: number): Promise<boolean> {
    const updated = await CpOrgHeadModel.updateOne(
      { _id: orgId, 'lease.token': token },
      { $set: { 'lease.until': until } },
    );
    return updated.matchedCount === 1;
  }

  async recordIntent(
    orgId: string,
    token: string,
    intent: ConfigChanged,
  ): Promise<boolean> {
    const updated = await CpOrgHeadModel.updateOne(
      { _id: orgId, 'lease.token': token },
      { $set: { 'lease.intent': intent } },
    );
    return updated.matchedCount === 1;
  }

  async advance(orgId: string, token: string, revision: number): Promise<void> {
    await CpOrgHeadModel.updateOne(
      { _id: orgId, 'lease.token': token },
      { $set: { revision, 'lease.intent': null, updatedAt: new Date() } },
    );
  }

  async finalize(
    orgId: string,
    token: string,
    revision: number,
  ): Promise<void> {
    await CpOrgHeadModel.updateOne(
      { _id: orgId, 'lease.token': token },
      { $set: { revision, lease: null, updatedAt: new Date() } },
    );
  }

  async release(
    orgId: string,
    token: string,
    keepIntent: boolean,
  ): Promise<void> {
    await CpOrgHeadModel.updateOne(
      { _id: orgId, 'lease.token': token },
      keepIntent ? { $set: { 'lease.until': 0 } } : { $set: { lease: null } },
    );
  }
}
