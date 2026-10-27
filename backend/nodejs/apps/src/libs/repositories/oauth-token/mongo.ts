import { Types } from 'mongoose';
import { OAuthAccessToken } from '../../../modules/oauth_provider/schema/oauth.access_token.schema';
import { OAuthRefreshToken } from '../../../modules/oauth_provider/schema/oauth.refresh_token.schema';
import { AuthorizationCode } from '../../../modules/oauth_provider/schema/authorization_code.schema';
import { OAuthDeviceCode } from '../../../modules/oauth_provider/schema/oauth.device_code.schema';
import { mapMongoError } from '../mongo';
import { toPlain } from '../plain';
import {
  OAuthDeviceCodeStatus,
  type AccessTokenDto,
  type AuthorizationCodeDto,
  type DeviceCodeDto,
  type NewAccessToken,
  type NewAuthorizationCode,
  type NewDeviceCode,
  type NewRefreshToken,
  type OAuthTokenRepository,
  type RefreshTokenDto,
} from './types';

const oid = (id: string): Types.ObjectId => new Types.ObjectId(id);
const liveSince = (): {
  isRevoked: { $eq: boolean };
  expiresAt: { $gt: Date };
} => ({
  isRevoked: { $eq: false },
  expiresAt: { $gt: new Date() },
});

export class MongoOAuthTokenRepository implements OAuthTokenRepository {
  async issueAccess(input: NewAccessToken): Promise<AccessTokenDto> {
    try {
      const stored = await OAuthAccessToken.create({
        tokenHash: input.tokenHash,
        clientId: input.clientId,
        userId: input.userId ? oid(input.userId) : undefined,
        orgId: oid(input.orgId),
        scopes: input.scopes,
        expiresAt: input.expiresAt,
        name: input.name,
        actorUserId:
          input.actorUserId === undefined ? undefined : oid(input.actorUserId),
        knowledgeLimit: input.knowledgeLimit,
      });
      return toPlain<AccessTokenDto>(stored);
    } catch (error) {
      throw mapMongoError(error);
    }
  }

  async issueRefresh(input: NewRefreshToken): Promise<RefreshTokenDto> {
    try {
      const stored = await OAuthRefreshToken.create({
        tokenHash: input.tokenHash,
        clientId: input.clientId,
        userId: oid(input.userId),
        orgId: oid(input.orgId),
        scopes: input.scopes,
        expiresAt: input.expiresAt,
        knowledgeLimit: input.knowledgeLimit,
      });
      return toPlain<RefreshTokenDto>(stored);
    } catch (error) {
      throw mapMongoError(error);
    }
  }

  async findRefreshByHash(tokenHash: string): Promise<RefreshTokenDto | null> {
    const stored = await OAuthRefreshToken.findOne({
      tokenHash: { $eq: tokenHash },
    });
    return stored ? toPlain<RefreshTokenDto>(stored) : null;
  }

  async rotateRefresh(id: string): Promise<boolean> {
    const result = await OAuthRefreshToken.updateOne(
      { _id: oid(id), isRevoked: false },
      { $set: { isRevoked: true, revokedAt: new Date() } },
    );
    return result.modifiedCount === 1;
  }

  async touchAccessLastUsed(id: string, at: Date): Promise<void> {
    await OAuthAccessToken.updateOne({ _id: oid(id) }, { lastUsedAt: at });
  }

  async revokeAccessByHash(
    tokenHash: string,
    clientId: string,
  ): Promise<boolean> {
    // RFC 7009: the token must have been issued to the requesting client
    const result = await OAuthAccessToken.updateOne(
      {
        tokenHash: { $eq: tokenHash },
        clientId: { $eq: clientId },
        isRevoked: { $eq: false },
      },
      { isRevoked: true, revokedAt: new Date() },
    );
    return result.modifiedCount > 0;
  }

  async revokeRefreshByHash(
    tokenHash: string,
    clientId: string,
  ): Promise<boolean> {
    const result = await OAuthRefreshToken.updateOne(
      {
        tokenHash: { $eq: tokenHash },
        clientId: { $eq: clientId },
        isRevoked: { $eq: false },
      },
      { isRevoked: true, revokedAt: new Date() },
    );
    return result.modifiedCount > 0;
  }

  async revokeIssued(accessHash: string, refreshHash?: string): Promise<void> {
    const revoked = { isRevoked: true, revokedAt: new Date() };
    await Promise.all([
      OAuthAccessToken.updateOne({ tokenHash: { $eq: accessHash } }, revoked),
      refreshHash
        ? OAuthRefreshToken.updateOne(
            { tokenHash: { $eq: refreshHash } },
            revoked,
          )
        : Promise.resolve(),
    ]);
  }

  async revokeRefreshWithoutKnowledgeLimit(clientId: string): Promise<void> {
    await OAuthRefreshToken.updateMany(
      {
        clientId: { $eq: clientId },
        isRevoked: { $eq: false },
        knowledgeLimit: { $exists: false },
      },
      { isRevoked: true, revokedAt: new Date() },
    );
  }

  async revokeAllForClient(clientId: string): Promise<void> {
    await Promise.all([
      OAuthAccessToken.updateMany(
        { clientId: { $eq: clientId }, isRevoked: { $eq: false } },
        { isRevoked: true, revokedAt: new Date() },
      ),
      OAuthRefreshToken.updateMany(
        { clientId: { $eq: clientId }, isRevoked: { $eq: false } },
        { isRevoked: true, revokedAt: new Date() },
      ),
    ]);
  }

  async revokeAllForUser(userId: string): Promise<void> {
    const userObjId = oid(userId);
    await Promise.all([
      OAuthAccessToken.updateMany(
        { userId: { $eq: userObjId }, isRevoked: { $eq: false } },
        { isRevoked: true, revokedAt: new Date() },
      ),
      OAuthRefreshToken.updateMany(
        { userId: { $eq: userObjId }, isRevoked: { $eq: false } },
        { isRevoked: true, revokedAt: new Date() },
      ),
    ]);
  }

  async revokeAllForClientUser(
    clientId: string,
    userId: string,
  ): Promise<void> {
    const userObjId = oid(userId);
    await Promise.all([
      OAuthAccessToken.updateMany(
        {
          clientId: { $eq: clientId },
          userId: { $eq: userObjId },
          isRevoked: { $eq: false },
        },
        { isRevoked: true, revokedAt: new Date() },
      ),
      OAuthRefreshToken.updateMany(
        {
          clientId: { $eq: clientId },
          userId: { $eq: userObjId },
          isRevoked: { $eq: false },
        },
        { isRevoked: true, revokedAt: new Date() },
      ),
    ]);
  }

  async revokeAllForClientUserBecause(
    clientId: string,
    userId: string,
    reason: string,
  ): Promise<void> {
    const userObjId = oid(userId);
    await Promise.all([
      OAuthAccessToken.updateMany(
        { clientId: { $eq: clientId }, userId: userObjId, isRevoked: false },
        { isRevoked: true, revokedAt: new Date(), revokedReason: reason },
      ),
      OAuthRefreshToken.updateMany(
        { clientId: { $eq: clientId }, userId: userObjId, isRevoked: false },
        { isRevoked: true, revokedAt: new Date(), revokedReason: reason },
      ),
    ]);
  }

  async revokeDelegatedForActor(
    actorUserId: string,
    revokedBy: string | undefined,
    reason: string,
  ): Promise<number> {
    const result = await OAuthAccessToken.updateMany(
      { actorUserId: oid(actorUserId), isRevoked: false },
      {
        $set: {
          isRevoked: true,
          revokedAt: new Date(),
          revokedReason: reason,
          ...(revokedBy === undefined ? {} : { revokedBy: oid(revokedBy) }),
        },
      },
    );
    return result.modifiedCount;
  }

  async revokeAccessById(
    id: string,
    clientId: string,
    userId: string,
    revokedBy: string,
    reason?: string,
  ): Promise<boolean> {
    // A malformed id would otherwise throw a BSONError out of the ObjectId
    // constructor; treat it like "not found".
    if (!Types.ObjectId.isValid(id)) {
      return false;
    }
    const result = await OAuthAccessToken.updateOne(
      {
        _id: { $eq: oid(id) },
        clientId: { $eq: clientId },
        userId: { $eq: oid(userId) },
        isRevoked: { $eq: false },
      },
      {
        isRevoked: true,
        revokedAt: new Date(),
        revokedBy: oid(revokedBy),
        revokedReason: reason,
      },
    );
    return result.modifiedCount > 0;
  }

  async revokeAccessByIdForClient(
    id: string,
    clientId: string,
    revokedBy: string,
    reason?: string,
  ): Promise<boolean> {
    if (!Types.ObjectId.isValid(id)) {
      return false;
    }
    const result = await OAuthAccessToken.updateOne(
      {
        _id: { $eq: oid(id) },
        clientId: { $eq: clientId },
        isRevoked: { $eq: false },
      },
      {
        isRevoked: true,
        revokedAt: new Date(),
        revokedBy: oid(revokedBy),
        revokedReason: reason,
      },
    );
    return result.modifiedCount > 0;
  }

  async listLiveForClient(
    clientId: string,
    limit: number,
  ): Promise<{ access: AccessTokenDto[]; refresh: RefreshTokenDto[] }> {
    const [access, refresh] = await Promise.all([
      OAuthAccessToken.find({ clientId: { $eq: clientId }, ...liveSince() })
        .sort({ createdAt: -1, _id: -1 })
        .limit(limit)
        .exec(),
      OAuthRefreshToken.find({ clientId: { $eq: clientId }, ...liveSince() })
        .sort({ createdAt: -1, _id: -1 })
        .limit(limit)
        .exec(),
    ]);
    return {
      access: access.map((t) => toPlain<AccessTokenDto>(t)),
      refresh: refresh.map((t) => toPlain<RefreshTokenDto>(t)),
    };
  }

  async listLiveAccessForUser(
    clientId: string,
    userId: string,
    limit: number,
  ): Promise<AccessTokenDto[]> {
    const tokens = await OAuthAccessToken.find({
      clientId: { $eq: clientId },
      userId: { $eq: oid(userId) },
      ...liveSince(),
    })
      .sort({ createdAt: -1, _id: -1 })
      .limit(limit)
      .exec();
    return tokens.map((t) => toPlain<AccessTokenDto>(t));
  }

  countLiveAccessForUser(clientId: string, userId: string): Promise<number> {
    return OAuthAccessToken.countDocuments({
      clientId: { $eq: clientId },
      userId: { $eq: oid(userId) },
      ...liveSince(),
    }).exec();
  }

  async listLiveAccessForClientPage(
    clientId: string,
    page: number,
    limit: number,
  ): Promise<{ rows: AccessTokenDto[]; total: number }> {
    const filter = { clientId: { $eq: clientId }, ...liveSince() };
    const [tokens, total] = await Promise.all([
      OAuthAccessToken.find(filter)
        .sort({ createdAt: -1, _id: -1 })
        .skip((page - 1) * limit)
        .limit(limit)
        .exec(),
      OAuthAccessToken.countDocuments(filter),
    ]);
    return { rows: tokens.map((t) => toPlain<AccessTokenDto>(t)), total };
  }

  async createCode(input: NewAuthorizationCode): Promise<void> {
    try {
      await AuthorizationCode.create({
        ...input,
        userId: oid(input.userId),
        orgId: oid(input.orgId),
      });
    } catch (error) {
      throw mapMongoError(error);
    }
  }

  async findCode(
    code: string,
    clientId: string,
  ): Promise<AuthorizationCodeDto | null> {
    const authCode = await AuthorizationCode.findOne({
      code: { $eq: code },
      clientId: { $eq: clientId },
    });
    return authCode ? toPlain<AuthorizationCodeDto>(authCode) : null;
  }

  async consumeCode(code: string, clientId: string): Promise<boolean> {
    const result = await AuthorizationCode.updateOne(
      {
        code: { $eq: code },
        clientId: { $eq: clientId },
        isUsed: false,
        expiresAt: { $gte: new Date() },
      },
      { $set: { isUsed: true, usedAt: new Date() } },
    );
    return result.modifiedCount === 1;
  }

  async flagCodeReplayed(code: string, clientId: string): Promise<void> {
    await AuthorizationCode.updateOne(
      { code: { $eq: code }, clientId: { $eq: clientId } },
      { $set: { replayedAt: new Date() } },
    );
  }

  async deleteCode(id: string): Promise<void> {
    await AuthorizationCode.deleteOne({ _id: oid(id) });
  }

  async deleteExpiredCodes(): Promise<number> {
    const result = await AuthorizationCode.deleteMany({
      $or: [{ expiresAt: { $lt: new Date() } }, { isUsed: true }],
    });
    return result.deletedCount;
  }

  async deleteUnusedCodesForUser(userId: string): Promise<void> {
    await AuthorizationCode.deleteMany({ userId: oid(userId), isUsed: false });
  }

  async deleteUnusedCodesForClient(clientId: string): Promise<void> {
    await AuthorizationCode.deleteMany({
      clientId: { $eq: clientId },
      isUsed: false,
    });
  }

  async createDevice(input: NewDeviceCode): Promise<void> {
    try {
      await OAuthDeviceCode.create({
        ...input,
        status: OAuthDeviceCodeStatus.PENDING,
      });
    } catch (error) {
      throw mapMongoError(error);
    }
  }

  async findDeviceByUserCode(userCode: string): Promise<DeviceCodeDto | null> {
    const record = await OAuthDeviceCode.findOne({ userCode });
    return record ? toPlain<DeviceCodeDto>(record) : null;
  }

  async findDeviceByHash(
    deviceCodeHash: string,
    clientId: string,
  ): Promise<DeviceCodeDto | null> {
    const record = await OAuthDeviceCode.findOne({
      deviceCodeHash: { $eq: deviceCodeHash },
      clientId: { $eq: clientId },
    });
    return record ? toPlain<DeviceCodeDto>(record) : null;
  }

  async decideDevice(
    id: string,
    decision:
      | { status: OAuthDeviceCodeStatus.DENIED }
      | {
          status: OAuthDeviceCodeStatus.APPROVED;
          userId: string;
          orgId: string;
        },
  ): Promise<void> {
    await OAuthDeviceCode.updateOne(
      { _id: oid(id) },
      {
        $set:
          decision.status === OAuthDeviceCodeStatus.APPROVED
            ? {
                status: decision.status,
                userId: oid(decision.userId),
                orgId: oid(decision.orgId),
              }
            : { status: decision.status },
      },
    );
  }

  async recordDevicePoll(id: string, at: Date): Promise<void> {
    await OAuthDeviceCode.updateOne(
      { _id: oid(id) },
      { $set: { lastPolledAt: at } },
    );
  }

  async claimApprovedDevice(id: string): Promise<DeviceCodeDto | null> {
    const claimed = await OAuthDeviceCode.findOneAndDelete({
      _id: oid(id),
      status: OAuthDeviceCodeStatus.APPROVED,
      expiresAt: { $gt: new Date() },
    });
    return claimed ? toPlain<DeviceCodeDto>(claimed) : null;
  }
}
