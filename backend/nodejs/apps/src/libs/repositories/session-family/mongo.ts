import { isDuplicateKeyError } from '../../utils/mongo.utils';
import { SessionFamilyModel } from './schema/session-family.schema';
import type {
  NewSessionFamily,
  SessionFamily,
  SessionFamilyRepository,
} from './types';

const date = (v: unknown): Date | null =>
  v === null || v === undefined ? null : new Date(v as Date);

const plain = (row: Record<string, unknown>): SessionFamily => ({
  orgId: String(row.orgId),
  familyId: String(row.familyId),
  userId: String(row.userId),
  currentTokenId: String(row.currentTokenId),
  previousTokenId:
    typeof row.previousTokenId === 'string' ? row.previousTokenId : null,
  rotatedAt: date(row.rotatedAt),
  expiresAt: new Date(row.expiresAt as Date),
  revokedAt: date(row.revokedAt),
  revokedReason:
    typeof row.revokedReason === 'string' ? row.revokedReason : null,
});

export class MongoSessionFamilyRepository implements SessionFamilyRepository {
  async start(family: NewSessionFamily): Promise<boolean> {
    // Without the unique index two racing starts of one family would both insert.
    await SessionFamilyModel.init();
    try {
      await SessionFamilyModel.create({
        ...family,
        revokedAt: null,
        revokedReason: null,
      });
      return true;
    } catch (error) {
      if (isDuplicateKeyError(error)) return false;
      throw error;
    }
  }

  async get(orgId: string, familyId: string): Promise<SessionFamily | null> {
    const row = await SessionFamilyModel.findOne({ orgId, familyId }).lean();
    return row ? plain(row as unknown as Record<string, unknown>) : null;
  }

  async rotate(
    orgId: string,
    familyId: string,
    fromTokenId: string,
    toTokenId: string,
    at: Date,
  ): Promise<boolean> {
    const { modifiedCount } = await SessionFamilyModel.updateOne(
      { orgId, familyId, currentTokenId: fromTokenId, revokedAt: null },
      {
        $set: {
          currentTokenId: toTokenId,
          previousTokenId: fromTokenId,
          rotatedAt: at,
        },
      },
    );
    return modifiedCount === 1;
  }

  async revoke(
    orgId: string,
    familyId: string,
    reason: string,
    at: Date,
  ): Promise<boolean> {
    const { modifiedCount } = await SessionFamilyModel.updateOne(
      { orgId, familyId, revokedAt: null },
      { $set: { revokedAt: at, revokedReason: reason } },
    );
    return modifiedCount === 1;
  }
}
