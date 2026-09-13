import { injectable, inject } from 'inversify';
import { Types } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import { OAuthRefreshToken } from '../schema/oauth.refresh_token.schema';
import { OAuthAccessToken } from '../schema/oauth.access_token.schema';
import { OAuthApp } from '../schema/oauth.app.schema';
import { Users } from '../../user_management/schema/users.schema';
import { PAT_APP_CLIENT_ID_PREFIX } from '../constants/constants';
import { NotFoundError } from '../../../libs/errors/http.errors';
import {
  OAuthGrantListItem,
  AdminOAuthGrantListItem,
  PaginatedResponse,
} from '../types/oauth.types';

interface UserLatestTokenGroup {
  _id: string;
  lastUsedAt?: Date;
}

interface AdminLatestTokenGroup {
  _id: { clientId: string; userId: Types.ObjectId };
  lastUsedAt?: Date;
}

/**
 * Service for listing and revoking active OAuth grants (sessions) issued
 * to third-party clients and first-party agents (e.g. via authorization code
 * or device authorization flows).
 *
 * Distinct from PatService, which manages personal access tokens minted
 * directly by users for themselves.
 */
@injectable()
export class OAuthGrantService {
  constructor(@inject('Logger') private logger: Logger) {}

  /**
   * List the calling user's active OAuth grants.
   */
  async listUserGrants(
    orgId: string,
    userId: string,
  ): Promise<OAuthGrantListItem[]> {
    const userObjId = new Types.ObjectId(userId);
    const orgObjId = new Types.ObjectId(orgId);
    const now = new Date();

    const [refreshTokens, accessTokens] = await Promise.all([
      OAuthRefreshToken.find({
        userId: { $eq: userObjId },
        orgId: { $eq: orgObjId },
        isRevoked: { $eq: false },
        expiresAt: { $gt: now },
      })
        .sort({ createdAt: -1 })
        .exec(),
      OAuthAccessToken.find({
        userId: { $eq: userObjId },
        orgId: { $eq: orgObjId },
        isRevoked: { $eq: false },
        expiresAt: { $gt: now },
        clientId: { $not: new RegExp(`^${PAT_APP_CLIENT_ID_PREFIX}`) },
      })
        .sort({ createdAt: -1 })
        .exec(),
    ]);

    const clientIds = Array.from(
      new Set([
        ...refreshTokens.map((t) => t.clientId),
        ...accessTokens.map((t) => t.clientId),
      ]),
    );

    const apps = await OAuthApp.find({
      clientId: { $in: clientIds },
    })
      .lean()
      .exec();
    const appsByClientId = new Map(apps.map((a) => [a.clientId, a]));

    // Find latest lastUsedAt for each client
    const latestAccessTokens =
      await OAuthAccessToken.aggregate<UserLatestTokenGroup>([
        {
          $match: {
            userId: userObjId,
            orgId: orgObjId,
            clientId: { $in: clientIds },
          },
        },
        {
          $group: {
            _id: '$clientId',
            lastUsedAt: { $max: '$lastUsedAt' },
          },
        },
      ]);
    const lastUsedByClientId = new Map<string, Date | undefined>(
      latestAccessTokens.map((t) => [t._id, t.lastUsedAt]),
    );

    const grants: OAuthGrantListItem[] = [];
    const clientsWithRefreshTokens = new Set<string>();

    for (const rt of refreshTokens) {
      clientsWithRefreshTokens.add(rt.clientId);
      const app = appsByClientId.get(rt.clientId);
      grants.push({
        id: (rt._id as Types.ObjectId).toString(),
        clientId: rt.clientId,
        appName: app?.name ?? rt.clientId,
        appDescription: app?.description,
        appLogoUrl: app?.logoUrl,
        isConfidential: app?.isConfidential ?? false,
        scopes: rt.scopes,
        createdAt: rt.createdAt,
        expiresAt: rt.expiresAt,
        lastUsedAt: lastUsedByClientId.get(rt.clientId),
      });
    }

    // Include access tokens for clients that do not have refresh tokens
    for (const at of accessTokens) {
      if (!clientsWithRefreshTokens.has(at.clientId)) {
        const app = appsByClientId.get(at.clientId);
        grants.push({
          id: (at._id as Types.ObjectId).toString(),
          clientId: at.clientId,
          appName: app?.name ?? at.clientId,
          appDescription: app?.description,
          appLogoUrl: app?.logoUrl,
          isConfidential: app?.isConfidential ?? false,
          scopes: at.scopes,
          createdAt: at.createdAt,
          expiresAt: at.expiresAt,
          lastUsedAt: at.lastUsedAt,
        });
      }
    }

    return grants.sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());
  }

  /**
   * Revoke an active OAuth grant owned by the calling user.
   */
  async revokeUserGrant(
    orgId: string,
    userId: string,
    grantId: string,
    reason?: string,
  ): Promise<void> {
    if (!Types.ObjectId.isValid(grantId)) {
      throw new NotFoundError('OAuth grant not found');
    }

    const grantObjId = new Types.ObjectId(grantId);
    const userObjId = new Types.ObjectId(userId);
    const orgObjId = new Types.ObjectId(orgId);

    // Check refresh tokens first
    const refreshToken = await OAuthRefreshToken.findOne({
      _id: { $eq: grantObjId },
      userId: { $eq: userObjId },
      orgId: { $eq: orgObjId },
      isRevoked: { $eq: false },
    });

    if (refreshToken) {
      refreshToken.isRevoked = true;
      refreshToken.revokedAt = new Date();
      refreshToken.revokedBy = userObjId;
      refreshToken.revokedReason = reason ?? 'Revoked by owner';
      await refreshToken.save();

      // Revoke any active access tokens for this user & client
      await OAuthAccessToken.updateMany(
        {
          userId: { $eq: userObjId },
          orgId: { $eq: orgObjId },
          clientId: { $eq: refreshToken.clientId },
          isRevoked: { $eq: false },
        },
        {
          isRevoked: true,
          revokedAt: new Date(),
          revokedBy: userObjId,
          revokedReason: reason ?? 'Revoked by owner',
        },
      );

      this.logger.info('OAuth grant revoked by owner', {
        orgId,
        userId,
        grantId,
        clientId: refreshToken.clientId,
      });
      return;
    }

    // Check standalone access token
    const accessToken = await OAuthAccessToken.findOne({
      _id: { $eq: grantObjId },
      userId: { $eq: userObjId },
      orgId: { $eq: orgObjId },
      clientId: { $not: new RegExp(`^${PAT_APP_CLIENT_ID_PREFIX}`) },
      isRevoked: { $eq: false },
    });

    if (accessToken) {
      accessToken.isRevoked = true;
      accessToken.revokedAt = new Date();
      accessToken.revokedBy = userObjId;
      accessToken.revokedReason = reason ?? 'Revoked by owner';
      await accessToken.save();

      this.logger.info('OAuth access token grant revoked by owner', {
        orgId,
        userId,
        grantId,
        clientId: accessToken.clientId,
      });
      return;
    }

    throw new NotFoundError('OAuth grant not found');
  }

  /**
   * List every active OAuth grant in the organization across all users.
   * Admin-only incident response endpoint.
   */
  async listAllGrants(
    orgId: string,
    page = 1,
    limit = 100,
  ): Promise<PaginatedResponse<AdminOAuthGrantListItem>> {
    const orgObjId = new Types.ObjectId(orgId);
    const now = new Date();

    const filter = {
      orgId: { $eq: orgObjId },
      isRevoked: { $eq: false },
      expiresAt: { $gt: now },
    };

    const [refreshTokens, total] = await Promise.all([
      OAuthRefreshToken.find(filter)
        .sort({ createdAt: -1 })
        .skip((page - 1) * limit)
        .limit(limit)
        .exec(),
      OAuthRefreshToken.countDocuments(filter),
    ]);

    const userIds = Array.from(
      new Set(refreshTokens.map((t) => t.userId.toString())),
    );
    const clientIds = Array.from(new Set(refreshTokens.map((t) => t.clientId)));

    const [owners, apps] = await Promise.all([
      Users.find({
        _id: { $in: userIds.map((id) => new Types.ObjectId(id)) },
      })
        .select('email fullName isDeleted')
        .lean()
        .exec(),
      OAuthApp.find({
        clientId: { $in: clientIds },
      })
        .lean()
        .exec(),
    ]);

    const ownersById = new Map(
      owners.map((u) => [(u._id as Types.ObjectId).toString(), u]),
    );
    const appsByClientId = new Map(apps.map((a) => [a.clientId, a]));

    // Find latest lastUsedAt per client/user pair
    const latestAccessTokens =
      await OAuthAccessToken.aggregate<AdminLatestTokenGroup>([
        {
          $match: {
            orgId: orgObjId,
            userId: { $in: userIds.map((id) => new Types.ObjectId(id)) },
            clientId: { $in: clientIds },
          },
        },
        {
          $group: {
            _id: { clientId: '$clientId', userId: '$userId' },
            lastUsedAt: { $max: '$lastUsedAt' },
          },
        },
      ]);

    const lastUsedMap = new Map(
      latestAccessTokens.map((t) => [
        `${t._id.clientId}:${t._id.userId.toString()}`,
        t.lastUsedAt,
      ]),
    );

    const data: AdminOAuthGrantListItem[] = refreshTokens.map((rt) => {
      const owner = ownersById.get(rt.userId.toString());
      const app = appsByClientId.get(rt.clientId);
      const lastUsedKey = `${rt.clientId}:${rt.userId.toString()}`;

      return {
        id: (rt._id as Types.ObjectId).toString(),
        clientId: rt.clientId,
        appName: app?.name ?? rt.clientId,
        appDescription: app?.description,
        appLogoUrl: app?.logoUrl,
        isConfidential: app?.isConfidential ?? false,
        scopes: rt.scopes,
        createdAt: rt.createdAt,
        expiresAt: rt.expiresAt,
        lastUsedAt: lastUsedMap.get(lastUsedKey),
        userId: rt.userId.toString(),
        ownerEmail: owner?.email,
        ownerFullName: owner?.fullName,
        ownerDeleted: !owner || owner.isDeleted === true,
      };
    });

    return {
      data,
      pagination: {
        page,
        limit,
        total,
        totalPages: Math.ceil(total / limit),
      },
    };
  }

  /**
   * Revoke any user's OAuth grant in the organization by id.
   * Admin-only counterpart to revokeUserGrant.
   */
  async adminRevokeGrant(
    orgId: string,
    adminUserId: string,
    grantId: string,
    reason?: string,
  ): Promise<void> {
    if (!Types.ObjectId.isValid(grantId)) {
      throw new NotFoundError('OAuth grant not found');
    }

    const grantObjId = new Types.ObjectId(grantId);
    const adminObjId = new Types.ObjectId(adminUserId);
    const orgObjId = new Types.ObjectId(orgId);

    const refreshToken = await OAuthRefreshToken.findOne({
      _id: { $eq: grantObjId },
      orgId: { $eq: orgObjId },
      isRevoked: { $eq: false },
    });

    if (refreshToken) {
      refreshToken.isRevoked = true;
      refreshToken.revokedAt = new Date();
      refreshToken.revokedBy = adminObjId;
      refreshToken.revokedReason = reason ?? 'Revoked by org admin';
      await refreshToken.save();

      await OAuthAccessToken.updateMany(
        {
          userId: { $eq: refreshToken.userId },
          orgId: { $eq: orgObjId },
          clientId: { $eq: refreshToken.clientId },
          isRevoked: { $eq: false },
        },
        {
          isRevoked: true,
          revokedAt: new Date(),
          revokedBy: adminObjId,
          revokedReason: reason ?? 'Revoked by org admin',
        },
      );

      this.logger.info('OAuth grant revoked by admin', {
        orgId,
        adminUserId,
        grantId,
        clientId: refreshToken.clientId,
      });
      return;
    }

    const accessToken = await OAuthAccessToken.findOne({
      _id: { $eq: grantObjId },
      orgId: { $eq: orgObjId },
      clientId: { $not: new RegExp(`^${PAT_APP_CLIENT_ID_PREFIX}`) },
      isRevoked: { $eq: false },
    });

    if (accessToken) {
      accessToken.isRevoked = true;
      accessToken.revokedAt = new Date();
      accessToken.revokedBy = adminObjId;
      accessToken.revokedReason = reason ?? 'Revoked by org admin';
      await accessToken.save();

      this.logger.info('OAuth access token grant revoked by admin', {
        orgId,
        adminUserId,
        grantId,
        clientId: accessToken.clientId,
      });
      return;
    }

    throw new NotFoundError('OAuth grant not found');
  }
}
