import { resolveOAuthTokenService } from '../../../libs/services/oauth-token-service.provider';

/** SEC-AZ-08: revokes every token a person holds, under every client (their PATs, OAuth apps' grants). */
export const USER_TOKEN_REVOKER = 'UserTokenRevoker';

export interface UserTokenRevoker {
  revokeEveryTokenForUser(userId: string): Promise<void>;
}

/**
 * The OAuth provider's token service, read when called: it is registered after
 * the user module's container is built. Without the OAuth provider no token
 * was issued, so there is nothing to revoke.
 */
export const oauthUserTokenRevoker: UserTokenRevoker = {
  async revokeEveryTokenForUser(userId) {
    await resolveOAuthTokenService()?.revokeEveryTokenForUser(userId);
  },
};
