import { WrappedDataKey } from '../../../../modules/agent-harness/domain/data-protection';
import { DataKeyStore } from '../../../../modules/agent-harness/ports/data-keys';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import { AgentHarnessDataKeyModel } from './schema/data-protection.schema';

const isDuplicateKey = (error: unknown): boolean =>
  typeof error === 'object' &&
  error !== null &&
  (error as { code?: unknown }).code === 11000;

/** Wrapped data keys; majority writes so a shred or a new version survives failover. */
export class MongoDataKeyStore implements DataKeyStore {
  async list(orgId: string): Promise<WrappedDataKey[]> {
    const documents = await AgentHarnessDataKeyModel.find({ orgId })
      .sort({ version: 1 })
      .read('primary')
      .readConcern('majority')
      .lean();
    return documents.map((document) => ({
      orgId: document.orgId,
      kid: document._id,
      version: document.version,
      kekId: document.kekId,
      wrapped: document.wrapped,
      createdAt: document.createdAt,
    }));
  }

  async insert(key: WrappedDataKey): Promise<boolean> {
    try {
      await new AgentHarnessDataKeyModel({
        _id: key.kid,
        orgId: key.orgId,
        version: key.version,
        kekId: key.kekId,
        wrapped: key.wrapped,
        createdAt: key.createdAt,
      }).save({ w: 'majority' });
      return true;
    } catch (error) {
      if (isDuplicateKey(error)) return false;
      throw error;
    }
  }

  async reserveSeals(
    orgId: string,
    kid: string,
    count: number,
  ): Promise<number | null> {
    const document = await AgentHarnessDataKeyModel.findOneAndUpdate(
      { _id: kid, orgId },
      { $inc: { seals: count } },
      { returnDocument: 'after', projection: { seals: 1 }, ...MAJORITY_WRITE },
    ).lean();
    return document === null ? null : (document.seals ?? count);
  }

  async deleteAll(orgId: string): Promise<number> {
    const result = await AgentHarnessDataKeyModel.deleteMany(
      { orgId },
      MAJORITY_WRITE,
    );
    return result.deletedCount;
  }

  async orgIds(): Promise<string[]> {
    const orgs = await AgentHarnessDataKeyModel.distinct('orgId')
      .read('primary')
      .readConcern('majority');
    return orgs.sort();
  }

  async replaceWrap(
    orgId: string,
    kid: string,
    expectedKekId: string,
    next: { kekId: string; wrapped: string },
  ): Promise<boolean> {
    const result = await AgentHarnessDataKeyModel.updateOne(
      { _id: kid, orgId, kekId: expectedKekId },
      { $set: { kekId: next.kekId, wrapped: next.wrapped } },
      MAJORITY_WRITE,
    );
    return result.modifiedCount === 1;
  }
}
