import { UserCredentials } from '../../../modules/auth/schema/userCredentials.schema';
import { toPlain } from '../plain';
import type { CredentialDto, CredentialRepository } from './types';

const dto = (row: unknown): CredentialDto => toPlain<CredentialDto>(row);

export class MongoCredentialRepository implements CredentialRepository {
  async find(userId: string, orgId: string): Promise<CredentialDto | null> {
    const row = await UserCredentials.findOne({
      userId,
      orgId,
      isDeleted: false,
    });
    return row ? dto(row) : null;
  }

  async clearBlock(userId: string, orgId: string): Promise<void> {
    const row = await UserCredentials.findOne({
      userId,
      orgId,
      isDeleted: false,
    });
    if (!row) return;
    row.isBlocked = false;
    row.wrongCredentialCount = 0;
    row.blockExpiresAt = null;
    await row.save();
  }

  async setBlocked(userId: string, orgId: string, until: Date): Promise<void> {
    const row = await UserCredentials.findOne({
      userId,
      orgId,
      isDeleted: false,
    });
    if (!row) return;
    row.isBlocked = true;
    row.blockExpiresAt = until;
    await row.save();
  }

  async reserveAttempt(
    userId: string,
    orgId: string,
    maxAttempts: number,
  ): Promise<CredentialDto | null> {
    const row = await UserCredentials.findOneAndUpdate(
      {
        userId,
        orgId,
        isDeleted: false,
        isBlocked: { $ne: true },
        wrongCredentialCount: { $lt: maxAttempts },
      },
      { $inc: { wrongCredentialCount: 1 } },
      { returnDocument: 'after' },
    );
    return row ? dto(row) : null;
  }

  async releaseAttempt(
    userId: string,
    orgId: string,
    reservedCount: number,
  ): Promise<void> {
    await UserCredentials.updateOne(
      {
        userId,
        orgId,
        isDeleted: false,
        isBlocked: { $ne: true },
        wrongCredentialCount: reservedCount,
      },
      { $set: { wrongCredentialCount: 0 } },
    );
  }

  async incrementWrongCount(
    userId: string,
    orgId: string,
  ): Promise<CredentialDto | null> {
    const row = await UserCredentials.findOneAndUpdate(
      { userId, orgId, isDeleted: false },
      { $inc: { wrongCredentialCount: 1 } },
      { returnDocument: 'after' },
    );
    return row ? dto(row) : null;
  }

  async claimOtp(
    userId: string,
    orgId: string,
    hashedOTP: string,
  ): Promise<CredentialDto | null> {
    const row = await UserCredentials.findOneAndUpdate(
      { userId, orgId, isDeleted: false, hashedOTP },
      { $unset: { hashedOTP: '', otpValidity: '' } },
      { returnDocument: 'after' },
    );
    return row ? dto(row) : null;
  }

  async saveOtp(
    userId: string,
    orgId: string,
    otp: { hashedOTP: string; otpValidity: number; ipAddress: string },
  ): Promise<void> {
    const row = await UserCredentials.findOne({
      orgId,
      userId,
      isDeleted: false,
    });
    if (!row) {
      await UserCredentials.create({
        orgId,
        userId,
        ipAddress: otp.ipAddress,
        hashedOTP: otp.hashedOTP,
        otpValidity: otp.otpValidity,
      });
      return;
    }
    row.hashedOTP = otp.hashedOTP;
    row.otpValidity = otp.otpValidity;
    await row.save();
  }

  async savePassword(
    userId: string,
    orgId: string,
    hashedPassword: string,
    ipAddress?: string,
  ): Promise<void> {
    let row = await UserCredentials.findOne({
      userId,
      orgId,
      isDeleted: false,
    });
    if (!row) {
      row = new UserCredentials();
      row.orgId = orgId;
      row.userId = userId;
    }
    row.hashedPassword = hashedPassword;
    if (ipAddress !== undefined && ipAddress !== '') {
      row.ipAddress = ipAddress;
    }
    await row.save();
  }

  async createWithPassword(input: {
    userId: string;
    orgId: string;
    hashedPassword: string;
    ipAddress: string | undefined;
  }): Promise<void> {
    await new UserCredentials({
      userId: input.userId,
      orgId: input.orgId,
      isDeleted: false,
      hashedPassword: input.hashedPassword,
      ipAddress: input.ipAddress,
    }).save();
  }

  async touchOnRefresh(
    userId: string,
    orgId: string,
    ipAddress: string | undefined,
  ): Promise<CredentialDto | null> {
    const row = await UserCredentials.findOneAndUpdate(
      { userId, orgId, isDeleted: false },
      { $set: { lastLogin: Date.now(), ipAddress } },
      { returnDocument: 'after', upsert: true },
    );
    return dto(row);
  }

  async unblock(userId: string, orgId: string): Promise<boolean> {
    const row = await UserCredentials.findOneAndUpdate(
      { userId, orgId, isBlocked: true, isDeleted: false },
      {
        $set: {
          isBlocked: false,
          wrongCredentialCount: 0,
          blockExpiresAt: null,
        },
      },
      { returnDocument: 'after' },
    );
    return !!row;
  }

  async blockedUserIds(orgId: string, among?: string[]): Promise<string[]> {
    const rows = await UserCredentials.find({
      orgId,
      ...(among === undefined ? {} : { userId: { $in: among } }),
      isBlocked: true,
      isDeleted: false,
    })
      .select('userId')
      .lean()
      .exec();
    return toPlain<Array<{ userId?: string | null }>>(rows).flatMap((r) =>
      r.userId === null || r.userId === undefined || r.userId === ''
        ? []
        : [r.userId],
    );
  }

  async userIdsWithPassword(
    orgId: string,
    userIds: string[],
  ): Promise<string[]> {
    const rows = await UserCredentials.find({
      orgId,
      isDeleted: { $ne: true },
      userId: { $in: userIds },
      hashedPassword: { $exists: true, $nin: [null, ''] },
    })
      .select('userId')
      .lean<Array<{ userId: string }>>();
    return rows.map((c) => c.userId);
  }

  async clearPassword(userId: string, _email: string): Promise<void> {
    await UserCredentials.updateOne(
      { userId },
      { $unset: { hashedPassword: '' } },
    );
  }

  async deleteForUser(userId: string, _email: string): Promise<void> {
    await UserCredentials.deleteMany({ userId });
  }

  async discardForUser(userId: string, _email: string): Promise<void> {
    await UserCredentials.deleteOne({ userId });
  }
}
