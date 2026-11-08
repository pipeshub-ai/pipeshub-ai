import { OAuthAccessToken } from '../../../modules/oauth_provider/schema/oauth.access_token.schema';
import { OAuthRefreshToken } from '../../../modules/oauth_provider/schema/oauth.refresh_token.schema';
import { OAuthApp } from '../../../modules/oauth_provider/schema/oauth.app.schema';
import { toPlain } from '../plain';
import type { AccessTokenDto, RefreshTokenDto } from '../oauth-token/types';
import type {
  IdentityLookup,
  ResolvedAccessToken,
  ResolvedClient,
  ResolvedRefreshToken,
} from './types';

export class MongoIdentityLookup implements IdentityLookup {
  async resolveToken(tokenHash: string): Promise<ResolvedAccessToken | null> {
    const row = await OAuthAccessToken.findOne({
      tokenHash: { $eq: tokenHash },
      isRevoked: { $eq: false },
    });
    if (!row) {
      return null;
    }
    const t = toPlain<AccessTokenDto>(row);
    return {
      _id: t._id,
      orgId: t.orgId,
      userId: t.userId,
      clientId: t.clientId,
      scopes: t.scopes,
      expiresAt: t.expiresAt,
      lastUsedAt: t.lastUsedAt,
    };
  }

  async resolveRefresh(
    tokenHash: string,
  ): Promise<ResolvedRefreshToken | null> {
    const row = await OAuthRefreshToken.findOne({
      tokenHash: { $eq: tokenHash },
      isRevoked: { $eq: false },
    });
    if (!row) {
      return null;
    }
    const t = toPlain<RefreshTokenDto>(row);
    return {
      _id: t._id,
      orgId: t.orgId,
      userId: t.userId,
      clientId: t.clientId,
      scopes: t.scopes,
      expiresAt: t.expiresAt,
      rotationCount: t.rotationCount,
    };
  }

  // Mongo needs no org to run a grant.
  resolveCodeOrg(): Promise<string | null> {
    return Promise.resolve(null);
  }

  resolveRefreshOrg(): Promise<string | null> {
    return Promise.resolve(null);
  }

  async resolveClient(clientId: string): Promise<ResolvedClient | null> {
    const app = await OAuthApp.findOne({
      clientId: { $eq: clientId },
      isDeleted: false,
    })
      .select('orgId status')
      .lean()
      .exec();
    return app
      ? { orgId: String(app.orgId), appId: String(app._id), status: app.status }
      : null;
  }
}
