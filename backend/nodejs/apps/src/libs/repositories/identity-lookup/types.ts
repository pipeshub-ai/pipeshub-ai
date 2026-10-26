import type { AccessTokenDto, RefreshTokenDto } from '../oauth-token/types';
import type { OAuthAppStatus } from '../oauth-app/types';

/**
 * What a live access token tells the caller before the org is known. A store
 * that answers from a vetted function (`oauth.resolve_token`) returns exactly
 * these columns, so a caller may not rely on more.
 */
export type ResolvedAccessToken = Pick<
  AccessTokenDto,
  | '_id'
  | 'orgId'
  | 'userId'
  | 'clientId'
  | 'scopes'
  | 'expiresAt'
  | 'lastUsedAt'
>;

export type ResolvedRefreshToken = Pick<
  RefreshTokenDto,
  | '_id'
  | 'orgId'
  | 'userId'
  | 'clientId'
  | 'scopes'
  | 'expiresAt'
  | 'rotationCount'
>;

export interface ResolvedClient {
  orgId: string;
  appId: string;
  status: OAuthAppStatus;
}

/**
 * The only calls allowed without an org bound, for the OAuth
 * lookups. Each returns at most one row and a store must fail
 * closed (throw) when a key matches more than one.
 */
export interface IdentityLookup {
  /** A live (not revoked) access token by the SHA-256 of the token. */
  resolveToken(tokenHash: string): Promise<ResolvedAccessToken | null>;
  /** A live (not revoked) refresh token by the SHA-256 of the token. */
  resolveRefresh(tokenHash: string): Promise<ResolvedRefreshToken | null>;
  /**
   * The org that issued an authorization code, or null (unknown code, or a store that needs no answer). The token endpoint runs a
   * grant as this org, not the client's: a client is not tied to one org.
   */
  resolveCodeOrg(code: string, clientId: string): Promise<string | null>;
  /** The org of a live refresh token (raw token), or null. */
  resolveRefreshOrg(refreshToken: string): Promise<string | null>;
  /** The org that owns a client id (including the instance-wide first-party client). */
  resolveClient(clientId: string): Promise<ResolvedClient | null>;
}
