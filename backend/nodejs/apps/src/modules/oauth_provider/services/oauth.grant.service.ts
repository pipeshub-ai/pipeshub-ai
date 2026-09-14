import { injectable, inject } from 'inversify';
import mongoose, { Types } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import {
  OAuthRefreshToken,
  IOAuthRefreshToken,
} from '../schema/oauth.refresh_token.schema';
import {
  OAuthAccessToken,
  IOAuthAccessToken,
} from '../schema/oauth.access_token.schema';
import { OAuthApp } from '../schema/oauth.app.schema';
import { Users } from '../../user_management/schema/users.schema';
import { PAT_APP_CLIENT_ID_PREFIX } from '../constants/constants';
import { NotFoundError } from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  OAuthGrantListItem,
  AdminOAuthGrantListItem,
  PaginatedResponse,
} from '../types/oauth.types';

interface LatestTokenGroup {
  _id: Types.ObjectId;
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
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('AppConfig') private appConfig: AppConfig,
  ) {}

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

    const activeRefreshTokenIds = refreshTokens.map(
      (rt) => rt.familyId || (rt._id as Types.ObjectId),
    );

    // Find latest lastUsedAt for each active grant
    const latestAccessTokens =
      await OAuthAccessToken.aggregate<LatestTokenGroup>([
        {
          $match: {
            userId: userObjId,
            orgId: orgObjId,
            parentRefreshTokenId: { $in: activeRefreshTokenIds },
            isRevoked: false,
            expiresAt: { $gt: new Date() },
          },
        },
        {
          $group: {
            _id: '$parentRefreshTokenId',
            lastUsedAt: { $max: '$lastUsedAt' },
          },
        },
      ]);
    const lastUsedByTokenId = new Map<string, Date | undefined>(
      latestAccessTokens.map((t) => [t._id.toString(), t.lastUsedAt]),
    );

    const grants: OAuthGrantListItem[] = [];
    const activeRefreshTokenIdsSet = new Set(
      refreshTokens.map((rt) => (rt._id as Types.ObjectId).toString()),
    );

    for (const rt of refreshTokens) {
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
        lastUsedAt: lastUsedByTokenId.get(
          (rt._id as Types.ObjectId).toString(),
        ),
      });
    }

    // Include access tokens that are not children of an active refresh token grant
    for (const at of accessTokens) {
      const parentId = at.parentRefreshTokenId
        ? at.parentRefreshTokenId.toString()
        : undefined;
      if (parentId === undefined || !activeRefreshTokenIdsSet.has(parentId)) {
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

      const updateFilter = {
        userId: { $eq: userObjId },
        orgId: { $eq: orgObjId },
        clientId: { $eq: refreshToken.clientId },
        parentRefreshTokenId: { $eq: refreshToken.familyId || grantObjId },
        isRevoked: { $eq: false },
      };
      const updateDoc = {
        isRevoked: true,
        revokedAt: new Date(),
        revokedBy: userObjId,
        revokedReason: reason ?? 'Revoked by owner',
      };

      await this.executeRevocation(refreshToken, updateFilter, updateDoc);

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
   * Admin-only incident response endpoint. Includes both active refresh token
   * grants and active standalone access token grants (excluding PATs).
   */
  async listAllGrants(
    orgId: string,
    page = 1,
    limit = 100,
  ): Promise<PaginatedResponse<AdminOAuthGrantListItem>> {
    const orgObjId = new Types.ObjectId(orgId);
    const now = new Date();
    const skip = Math.max(0, (page - 1) * limit);
    const patPrefixRegex = new RegExp(`^${PAT_APP_CLIENT_ID_PREFIX}`);

    const [facetResult] = await OAuthRefreshToken.aggregate<{
      metadata: [{ total: number }] | [];
      data: Array<{
        _id: Types.ObjectId;
        clientId: string;
        userId?: Types.ObjectId;
        orgId: Types.ObjectId;
        scopes: string[];
        createdAt: Date;
        expiresAt: Date;
        lastUsedAt?: Date;
        familyId?: Types.ObjectId;
        type: 'refresh' | 'access';
      }>;
    }>([
      {
        $match: {
          orgId: { $eq: orgObjId },
          isRevoked: { $eq: false },
          expiresAt: { $gt: now },
        },
      },
      {
        $project: {
          _id: 1,
          clientId: 1,
          userId: 1,
          orgId: 1,
          scopes: 1,
          createdAt: 1,
          expiresAt: 1,
          familyId: 1,
          type: { $literal: 'refresh' },
        },
      },
      {
        $unionWith: {
          coll: 'oauthAccessTokens',
          pipeline: [
            {
              $match: {
                orgId: { $eq: orgObjId },
                isRevoked: { $eq: false },
                expiresAt: { $gt: now },
                clientId: { $not: patPrefixRegex },
              },
            },
            {
              $lookup: {
                from: 'oauthRefreshTokens',
                let: { parentId: '$parentRefreshTokenId' },
                pipeline: [
                  {
                    $match: {
                      $expr: {
                        $and: [
                          { $eq: ['$_id', '$$parentId'] },
                          { $eq: ['$isRevoked', false] },
                          { $gt: ['$expiresAt', now] },
                        ],
                      },
                    },
                  },
                ],
                as: 'activeParent',
              },
            },
            {
              $match: {
                activeParent: { $size: 0 },
              },
            },
            {
              $project: {
                _id: 1,
                clientId: 1,
                userId: 1,
                orgId: 1,
                scopes: 1,
                createdAt: 1,
                expiresAt: 1,
                lastUsedAt: 1,
                type: { $literal: 'access' },
              },
            },
          ],
        },
      },
      {
        $sort: { createdAt: -1, _id: -1 },
      },
      {
        $facet: {
          metadata: [{ $count: 'total' }],
          data: [{ $skip: skip }, { $limit: limit }],
        },
      },
    ]);

    const total = facetResult?.metadata?.[0]?.total ?? 0;
    const paginatedItems = facetResult?.data ?? [];

    const userIds = Array.from(
      new Set(
        paginatedItems
          .map((item) => item.userId?.toString())
          .filter((id): id is string => typeof id === 'string'),
      ),
    );
    const clientIds = Array.from(
      new Set(paginatedItems.map((item) => item.clientId)),
    );

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

    const refreshItems = paginatedItems.filter(
      (item) => item.type === 'refresh',
    );
    let lastUsedMap = new Map<string, Date | undefined>();
    if (refreshItems.length > 0) {
      const refreshItemIds = refreshItems.map(
        (r) => r.familyId || (r._id as Types.ObjectId),
      );
      const latestAccessTokens =
        await OAuthAccessToken.aggregate<LatestTokenGroup>([
          {
            $match: {
              orgId: orgObjId,
              userId: { $in: userIds.map((id) => new Types.ObjectId(id)) },
              parentRefreshTokenId: { $in: refreshItemIds },
              isRevoked: false,
              expiresAt: { $gt: new Date() },
            },
          },
          {
            $group: {
              _id: '$parentRefreshTokenId',
              lastUsedAt: { $max: '$lastUsedAt' },
            },
          },
        ]);
      lastUsedMap = new Map(
        latestAccessTokens.map((t) => [t._id.toString(), t.lastUsedAt]),
      );
    }

    const data: AdminOAuthGrantListItem[] = paginatedItems.map((item) => {
      const userStr = item.userId ? item.userId.toString() : '';
      const owner = item.userId ? ownersById.get(userStr) : undefined;
      const app = appsByClientId.get(item.clientId);

      const lastUsedAt =
        item.type === 'access'
          ? item.lastUsedAt
          : lastUsedMap.get(item._id.toString());

      return {
        id: item._id.toString(),
        clientId: item.clientId,
        appName: app?.name ?? item.clientId,
        appDescription: app?.description,
        appLogoUrl: app?.logoUrl,
        isConfidential: app?.isConfidential ?? false,
        scopes: item.scopes,
        createdAt: item.createdAt,
        expiresAt: item.expiresAt,
        lastUsedAt,
        userId: userStr,
        ownerEmail: owner?.email,
        ownerFullName: owner?.fullName,
        ownerDeleted: !owner || owner.isDeleted === true,
      };
    });

    const totalPages = total === 0 ? 1 : Math.ceil(total / limit);

    return {
      data,
      pagination: {
        page,
        limit,
        total,
        totalPages,
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

      const updateFilter = {
        userId: { $eq: refreshToken.userId },
        orgId: { $eq: orgObjId },
        clientId: { $eq: refreshToken.clientId },
        parentRefreshTokenId: { $eq: refreshToken.familyId || grantObjId },
        isRevoked: { $eq: false },
      };
      const updateDoc = {
        isRevoked: true,
        revokedAt: new Date(),
        revokedBy: adminObjId,
        revokedReason: reason ?? 'Revoked by org admin',
      };

      await this.executeRevocation(refreshToken, updateFilter, updateDoc);

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

  private async executeRevocation(
    refreshToken: mongoose.Document & IOAuthRefreshToken,
    updateFilter: mongoose.FilterQuery<IOAuthAccessToken>,
    updateDoc: mongoose.UpdateQuery<IOAuthAccessToken>,
  ): Promise<void> {
    if (this.appConfig.rsAvailable === 'true') {
      const session = await mongoose.startSession();
      try {
        await session.withTransaction(async () => {
          await refreshToken.save({ session });
          await OAuthAccessToken.updateMany(updateFilter, updateDoc, {
            session,
          });
        });
      } finally {
        await session.endSession();
      }
    } else {
      await OAuthAccessToken.updateMany(updateFilter, updateDoc);
      await refreshToken.save();
    }
  }
}
