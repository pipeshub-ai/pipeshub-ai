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
  OAuthGrantType,
  type NewOAuthApp,
  type OAuthAppDto,
  type OAuthAppPatch,
  type OAuthAppRepository,
  type OrgAppListQuery,
  type OwnedAppListQuery,
  type UpdateOwnedOptions,
} from './types';

const oid = (id: string): Types.ObjectId => new Types.ObjectId(id);
const ID_FIELDS = new Set(['tokenIdentityUserId', 'deletedBy']);

/**
 * The apps a person manages in Developer Settings (index `{orgId, createdBy, isDeleted, createdAt}`); without a
 * creator, every such app of the org.
 */
const ownedFilter = (
  orgId: string,
  userId?: string,
): Record<string, unknown> => ({
  orgId: oid(orgId),
  isDeleted: false,
  ...(userId !== undefined && { createdBy: oid(userId) }),
  clientId: {
    $nin: [FIRST_PARTY_DEVICE_CLIENT_ID],
    $not: new RegExp(
      `^(?:${PAT_APP_CLIENT_ID_PREFIX}|${SERVICE_TOKEN_APP_CLIENT_ID_PREFIX})`,
    ),
  },
  isDynamic: { $ne: true },
});

/** Live apps the user created whose own `client_credentials` tokens act as them: no service account named. */
const actingAsCreatorFilter = (
  orgId: string,
  userId: string,
): Record<string, unknown> => ({
  orgId: oid(orgId),
  createdBy: oid(userId),
  isDeleted: false,
  allowedGrantTypes: OAuthGrantType.CLIENT_CREDENTIALS,
  tokenIdentityUserId: null,
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
    return this.listWhere(ownedFilter(orgId, userId), q);
  }

  async findInOrg(orgId: string, appId: string): Promise<OAuthAppDto | null> {
    const app = await OAuthApp.findOne({
      _id: oid(appId),
      ...ownedFilter(orgId),
    });
    return app ? toPlain<OAuthAppDto>(app) : null;
  }

  async listInOrg(
    orgId: string,
    q: OrgAppListQuery,
  ): Promise<{ rows: OAuthAppDto[]; total: number }> {
    return this.listWhere(ownedFilter(orgId, q.createdBy), q);
  }

  private async listWhere(
    filter: Record<string, unknown>,
    q: OwnedAppListQuery,
  ): Promise<{ rows: OAuthAppDto[]; total: number }> {
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

  async setDynamicGrant(
    orgId: string,
    id: string,
    grant: Pick<OAuthAppDto, 'allowedScopes' | 'allowedGrantTypes'>,
  ): Promise<void> {
    await OAuthApp.updateOne(
      { _id: oid(id), orgId: oid(orgId), isDynamic: true, isDeleted: false },
      {
        $set: {
          allowedScopes: grant.allowedScopes,
          allowedGrantTypes: grant.allowedGrantTypes,
        },
      },
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

  async listClientIdsActingAsCreator(
    orgId: string,
    userId: string,
  ): Promise<string[]> {
    const apps = await OAuthApp.find(actingAsCreatorFilter(orgId, userId))
      .select('clientId')
      .lean()
      .exec();
    return apps.map((a) => a.clientId);
  }

  async softDeleteActingAsCreator(
    orgId: string,
    userId: string,
    deletedBy: string,
  ): Promise<void> {
    await OAuthApp.updateMany(actingAsCreatorFilter(orgId, userId), {
      $set: {
        isDeleted: true,
        status: OAuthAppStatus.REVOKED,
        deletedBy: oid(deletedBy),
      },
    });
  }

  async transferCreatedBy(
    orgId: string,
    fromUserId: string,
    toUserId: string,
  ): Promise<void> {
    await OAuthApp.updateMany(
      { orgId: oid(orgId), createdBy: oid(fromUserId), isDeleted: false },
      { $set: { createdBy: oid(toUserId) } },
    );
  }
}
