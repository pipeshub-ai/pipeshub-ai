import mongoose, { Types } from 'mongoose';
import { OAuthApp } from '../../../modules/oauth_provider/schema/oauth.app.schema';
import {
  FIRST_PARTY_DEVICE_CLIENT_ID,
  PAT_APP_CLIENT_ID_PREFIX,
  SERVICE_TOKEN_APP_CLIENT_ID_PREFIX,
} from '../../../modules/oauth_provider/constants/constants';
import type { KnowledgeLimit } from '../../../modules/oauth_provider/types/oauth.types';
import { ConflictError } from '../../errors/http.errors';
import { storedKnowledgeLimitFilter } from '../../../modules/oauth_provider/types/knowledge_limit';
import { mapMongoError } from '../mongo';
import { toPlain } from '../plain';
import {
  OAuthAppStatus,
  type NewOAuthApp,
  type OAuthAppDto,
  type OAuthAppPatch,
  type OAuthAppRepository,
  type OwnedAppListQuery,
  type UpdateOwnedOptions,
} from './types';

const oid = (id: string): Types.ObjectId => new Types.ObjectId(id);
const ID_FIELDS = new Set(['tokenIdentityUserId', 'deletedBy']);

/** The apps a person manages in Developer Settings (index `{orgId, createdBy, isDeleted, createdAt}`). */
const ownedFilter = (
  orgId: string,
  userId: string,
): Record<string, unknown> => ({
  orgId: oid(orgId),
  isDeleted: false,
  createdBy: oid(userId),
  clientId: {
    $nin: [FIRST_PARTY_DEVICE_CLIENT_ID],
    $not: new RegExp(
      `^(?:${PAT_APP_CLIENT_ID_PREFIX}|${SERVICE_TOKEN_APP_CLIENT_ID_PREFIX})`,
    ),
  },
  isDynamic: { $ne: true },
});

export class MongoOAuthAppRepository implements OAuthAppRepository {
  async create(input: NewOAuthApp): Promise<OAuthAppDto> {
    try {
      const app = await OAuthApp.create({
        ...input,
        orgId: oid(input.orgId),
        createdBy: oid(input.createdBy),
        ...(input.tokenIdentityUserId !== undefined && {
          tokenIdentityUserId: oid(input.tokenIdentityUserId),
        }),
      });
      return toPlain<OAuthAppDto>(app);
    } catch (error) {
      throw mapMongoError(error);
    }
  }

  async findOwned(
    orgId: string,
    userId: string,
    appId: string,
  ): Promise<OAuthAppDto | null> {
    const app = await OAuthApp.findOne({
      _id: oid(appId),
      ...ownedFilter(orgId, userId),
    });
    return app ? toPlain<OAuthAppDto>(app) : null;
  }

  async listOwned(
    orgId: string,
    userId: string,
    q: OwnedAppListQuery,
  ): Promise<{ rows: OAuthAppDto[]; total: number }> {
    const filter = ownedFilter(orgId, userId);
    if (q.status) {
      filter.status = { $eq: q.status };
    }
    if (q.search) {
      // Escape special regex characters to prevent ReDoS attacks
      const escapedSearch = q.search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      filter.$or = [
        { name: { $regex: escapedSearch, $options: 'i' } },
        { description: { $regex: escapedSearch, $options: 'i' } },
      ];
    }
    const [apps, total] = await Promise.all([
      OAuthApp.find(filter)
        .sort({ createdAt: -1, _id: -1 })
        .skip(q.skip)
        .limit(q.limit)
        .exec(),
      OAuthApp.countDocuments(filter),
    ]);
    return { rows: apps.map((a) => toPlain<OAuthAppDto>(a)), total };
  }

  async updateOwned(
    orgId: string,
    userId: string,
    appId: string,
    patch: OAuthAppPatch,
    opts?: UpdateOwnedOptions,
  ): Promise<OAuthAppDto | null> {
    const app = await OAuthApp.findOne({
      _id: oid(appId),
      ...ownedFilter(orgId, userId),
    });
    if (!app) {
      return null;
    }
    const target = app as unknown as Record<string, unknown>;
    for (const [key, value] of Object.entries(patch)) {
      target[key] =
        ID_FIELDS.has(key) && typeof value === 'string' ? oid(value) : value;
    }
    if (opts && 'overKnowledgeLimit' in opts) {
      app.$where = storedKnowledgeLimitFilter(opts.overKnowledgeLimit);
    }
    try {
      await app.save();
    } catch (error) {
      if (error instanceof mongoose.Error.DocumentNotFoundError) {
        if (opts?.ignoreStale) return toPlain<OAuthAppDto>(app);
        throw new ConflictError(
          'This application changed while you were editing it. Reload it and save again.',
        );
      }
      throw mapMongoError(error);
    }
    return toPlain<OAuthAppDto>(app);
  }

  async findLiveByClientId(clientId: string): Promise<OAuthAppDto | null> {
    const app = await OAuthApp.findOne({
      clientId: { $eq: clientId },
      isDeleted: false,
    });
    return app ? toPlain<OAuthAppDto>(app) : null;
  }

  async findLiveAllowedScopes(clientId: string): Promise<{
    allowedScopes: string[];
    knowledgeLimit?: KnowledgeLimit;
  } | null> {
    const app = await OAuthApp.findOne({
      clientId: { $eq: clientId },
      isDeleted: false,
    })
      .select('allowedScopes knowledgeLimit')
      .lean()
      .exec();
    return app
      ? {
          allowedScopes: app.allowedScopes,
          ...(app.knowledgeLimit !== undefined && {
            knowledgeLimit: app.knowledgeLimit,
          }),
        }
      : null;
  }

  async findLiveIdentity(
    clientId: string,
  ): Promise<{ createdBy: string; tokenIdentityUserId?: string } | null> {
    const app = await OAuthApp.findOne({ clientId, isDeleted: false })
      .select('createdBy tokenIdentityUserId')
      .lean()
      .exec();
    return app ? toPlain(app) : null;
  }

  async findByClientId(clientId: string): Promise<OAuthAppDto | null> {
    const app = await OAuthApp.findOne({ clientId: { $eq: clientId } });
    return app ? toPlain<OAuthAppDto>(app) : null;
  }

  async addAllowedScopes(id: string, scopes: string[]): Promise<void> {
    await OAuthApp.updateOne(
      { _id: id },
      { $addToSet: { allowedScopes: { $each: scopes } } },
    );
  }

  async listClientIdsActingAs(userId: string): Promise<string[]> {
    const apps = await OAuthApp.find({
      tokenIdentityUserId: userId,
      isDeleted: false,
    })
      .select('clientId')
      .lean()
      .exec();
    return apps.map((a) => a.clientId);
  }

  async listClientIdsCreatedBy(
    orgId: string,
    userId: string,
  ): Promise<string[]> {
    const apps = await OAuthApp.find({
      orgId: oid(orgId),
      createdBy: oid(userId),
      isDeleted: false,
    })
      .select('clientId')
      .lean()
      .exec();
    return apps.map((a) => a.clientId);
  }

  async softDeleteCreatedBy(
    orgId: string,
    userId: string,
    deletedBy: string,
  ): Promise<void> {
    await OAuthApp.updateMany(
      { orgId: oid(orgId), createdBy: oid(userId), isDeleted: false },
      {
        $set: {
          isDeleted: true,
          status: OAuthAppStatus.REVOKED,
          deletedBy: oid(deletedBy),
        },
      },
    );
  }
}
