import type { KnowledgeLimit } from '../../../modules/oauth_provider/types/oauth.types';

/** Stored on a refresh token when rotation succeeds. A later present is reuse. */
export const REFRESH_ROTATED = 'rotated';

/** Stored on every token in a grant when a rotated refresh token is presented again. */
export const REFRESH_REUSE_DETECTED = 'refresh_reuse_detected';

export enum OAuthDeviceCodeStatus {
  PENDING = 'pending',
  APPROVED = 'approved',
  DENIED = 'denied',
}

/** An access token row (also personal and service tokens). Only the hash of the token is stored. */
export interface AccessTokenDto {
  _id: string;
  tokenHash: string;
  clientId: string;
  userId?: string;
  orgId: string;
  scopes: string[];
  expiresAt: Date;
  isRevoked: boolean;
  revokedAt?: Date;
  revokedBy?: string;
  revokedReason?: string;
  parentRefreshTokenId?: string;
  name?: string;
  lastUsedAt?: Date;
  /** The service account that obtained a delegated token (`userId` is the person it speaks as). */
  actorUserId?: string;
  /** What the token may reach; absent means everything its user can see. */
  knowledgeLimit?: KnowledgeLimit;
  createdAt: Date;
  updatedAt: Date;
  __v: number;
}

export interface RefreshTokenDto {
  _id: string;
  tokenHash: string;
  clientId: string;
  userId: string;
  orgId: string;
  scopes: string[];
  expiresAt: Date;
  isRevoked: boolean;
  revokedAt?: Date;
  revokedBy?: string;
  revokedReason?: string;
  rotationCount: number;
  previousTokenHash?: string;
  /** The limit the user agreed to, which each refresh carries forward. `null` is no limit; absent marks a token stored before this was kept. */
  knowledgeLimit?: KnowledgeLimit | null;
  createdAt: Date;
  updatedAt: Date;
  __v: number;
}

export interface AuthorizationCodeDto {
  _id: string;
  code: string;
  codeChallenge?: string;
  codeChallengeMethod?: 'S256' | 'plain';
  clientId: string;
  userId: string;
  orgId: string;
  redirectUri: string;
  scopes: string[];
  /** What the token may reach; absent means everything its user can see. */
  knowledgeLimit?: KnowledgeLimit;
  expiresAt: Date;
  isUsed: boolean;
  usedAt?: Date;
  /** Set when the code was presented again after it was redeemed (or while it was being redeemed). */
  replayedAt?: Date;
  createdAt: Date;
  updatedAt: Date;
  __v: number;
}

/** A device authorization. Pending rows have no user or org; both are set on approval. */
export interface DeviceCodeDto {
  _id: string;
  deviceCodeHash: string;
  userCode: string;
  clientId: string;
  scopes: string[];
  status: OAuthDeviceCodeStatus;
  userId?: string;
  orgId?: string;
  /** What the token may reach; absent means everything its user can see. */
  knowledgeLimit?: KnowledgeLimit;
  interval: number;
  lastPolledAt?: Date;
  expiresAt: Date;
  createdAt: Date;
  updatedAt: Date;
  __v: number;
}

export interface NewAccessToken {
  tokenHash: string;
  clientId: string;
  userId?: string;
  orgId: string;
  scopes: string[];
  expiresAt: Date;
  name?: string;
  actorUserId?: string;
  /** What the token may reach; absent means everything its user can see. */
  knowledgeLimit?: KnowledgeLimit;
}

export interface NewRefreshToken {
  tokenHash: string;
  clientId: string;
  userId: string;
  orgId: string;
  scopes: string[];
  expiresAt: Date;
  knowledgeLimit?: KnowledgeLimit | null;
}

export interface NewAuthorizationCode {
  code: string;
  clientId: string;
  userId: string;
  orgId: string;
  redirectUri: string;
  scopes: string[];
  /** What the token may reach; absent means everything its user can see. */
  knowledgeLimit?: KnowledgeLimit;
  expiresAt: Date;
  codeChallenge?: string;
  codeChallengeMethod?: 'S256';
}

export interface NewDeviceCode {
  deviceCodeHash: string;
  userCode: string;
  clientId: string;
  scopes: string[];
  /** What the token may reach; absent means everything its user can see. */
  knowledgeLimit?: KnowledgeLimit;
  interval: number;
  expiresAt: Date;
}

/**
 * Tokens, authorization codes and device authorizations.
 *
 * Most methods are keyed by a token hash, a code or a client id, because the
 * caller does not know the org yet (the token endpoints, the revocation
 * endpoint). A store that needs the org first derives it from the key itself
 * (`oauth.resolve_*` / `oauth.consume_code` / `oauth.device_*` on PostgreSQL),
 * so callers never pass one for those. Reading a live token or refresh token by
 * its hash is `IdentityLookup.resolveToken` / `resolveRefresh`.
 */
export interface OAuthTokenRepository {
  // issue
  issueAccess(input: NewAccessToken): Promise<AccessTokenDto>;
  issueRefresh(input: NewRefreshToken): Promise<RefreshTokenDto>;
  /** The refresh token with this hash, revoked or not. */
  findRefreshByHash(tokenHash: string): Promise<RefreshTokenDto | null>;
  /**
   * Rotation: revokes a still-live refresh token and stamps `rotated`, as one
   * conditional write. True for exactly one of any number of concurrent callers.
   */
  rotateRefresh(id: string): Promise<boolean>;
  /**
   * Marks a refresh token as replayed after it was already rotated. The
   * winner of that rotation re-reads the row and kills its new pair.
   */
  flagRefreshReplayed(id: string): Promise<void>;
  /** Best-effort recency stamp; callers throttle. */
  touchAccessLastUsed(id: string, at: Date): Promise<void>;

  // revoke
  /** Revokes a still-live access token issued to this client. True when one was revoked. */
  revokeAccessByHash(tokenHash: string, clientId: string): Promise<boolean>;
  revokeRefreshByHash(tokenHash: string, clientId: string): Promise<boolean>;
  /** Revokes freshly issued tokens by hash (the app's scopes were withdrawn during the grant). */
  revokeIssued(accessHash: string, refreshHash?: string): Promise<void>;
  revokeAllForClient(clientId: string): Promise<void>;
  /** The client's live refresh tokens stored before they kept the user's knowledge limit. */
  revokeRefreshWithoutKnowledgeLimit(clientId: string): Promise<void>;
  /** Every token the user holds, under every client. */
  revokeAllForUser(userId: string): Promise<void>;
  revokeAllForClientUser(clientId: string, userId: string): Promise<void>;
  /** The same, recording why (authorization-code or refresh-token reuse). */
  revokeAllForClientUserBecause(
    clientId: string,
    userId: string,
    reason: string,
  ): Promise<void>;
  /** Live delegated tokens obtained by one service account; returns how many were revoked. */
  revokeDelegatedForActor(
    actorUserId: string,
    revokedBy: string | undefined,
    reason: string,
  ): Promise<number>;
  /** By row id, only for this client and user. */
  revokeAccessById(
    id: string,
    clientId: string,
    userId: string,
    revokedBy: string,
    reason?: string,
  ): Promise<boolean>;
  /** By row id, only for this client (administrator). */
  revokeAccessByIdForClient(
    id: string,
    clientId: string,
    revokedBy: string,
    reason?: string,
  ): Promise<boolean>;

  // list
  /** Newest first, at most `limit` of each kind. */
  listLiveForClient(
    clientId: string,
    limit: number,
  ): Promise<{ access: AccessTokenDto[]; refresh: RefreshTokenDto[] }>;
  listLiveAccessForUser(
    clientId: string,
    userId: string,
    limit: number,
  ): Promise<AccessTokenDto[]>;
  countLiveAccessForUser(clientId: string, userId: string): Promise<number>;
  listLiveAccessForClientPage(
    clientId: string,
    page: number,
    limit: number,
  ): Promise<{ rows: AccessTokenDto[]; total: number }>;

  // authorization codes
  createCode(input: NewAuthorizationCode): Promise<void>;
  /** The code issued to this client, used or not. */
  findCode(
    code: string,
    clientId: string,
  ): Promise<AuthorizationCodeDto | null>;
  /**
   * Redeems an unused, unexpired code as one conditional write. True for exactly
   * one of any number of concurrent callers; false means the code was already
   * used (or is gone), which the caller treats as a replay.
   */
  consumeCode(code: string, clientId: string): Promise<boolean>;
  /**
   * Records that the code was replayed. The redeemer that won reads this after
   * minting and revokes its own tokens, so a replay detected while the winner
   * was still minting is not lost.
   */
  flagCodeReplayed(code: string, clientId: string): Promise<void>;
  deleteCode(id: string): Promise<void>;
  /** Deletes expired and used codes; returns how many. */
  deleteExpiredCodes(): Promise<number>;
  deleteUnusedCodesForUser(userId: string): Promise<void>;
  deleteUnusedCodesForClient(clientId: string): Promise<void>;

  // device authorizations (not tied to an org until approved)
  createDevice(input: NewDeviceCode): Promise<void>;
  findDeviceByUserCode(userCode: string): Promise<DeviceCodeDto | null>;
  findDeviceByHash(
    deviceCodeHash: string,
    clientId: string,
  ): Promise<DeviceCodeDto | null>;
  /** Denies, or approves for this user in this org. */
  decideDevice(
    id: string,
    decision:
      | { status: OAuthDeviceCodeStatus.DENIED }
      | {
          status: OAuthDeviceCodeStatus.APPROVED;
          userId: string;
          orgId: string;
        },
  ): Promise<void>;
  recordDevicePoll(id: string, at: Date): Promise<void>;
  /** Deletes and returns an approved, unexpired authorization, so two polls cannot both issue tokens. */
  claimApprovedDevice(id: string): Promise<DeviceCodeDto | null>;
}
