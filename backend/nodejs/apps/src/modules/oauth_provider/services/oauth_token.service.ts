import { injectable, inject } from 'inversify';
import jwt, { Algorithm, Secret } from 'jsonwebtoken';
import crypto from 'crypto';
import { randomUUID } from 'crypto';
import { Logger } from '../../../libs/services/logger.service';
import {
  getOAuthTokenRepository,
  type AccessTokenDto,
} from '../../../libs/repositories/oauth-token';
import {
  getOAuthAppRepository,
  type OAuthAppDto,
} from '../../../libs/repositories/oauth-app';
import { getIdentityLookup } from '../../../libs/repositories/identity-lookup';
import {
  isKnowledgeLimitWithin,
  toKnowledgeLimit,
} from '../types/knowledge_limit';
import {
  InvalidGrantError,
  InvalidTokenError,
  ExpiredTokenError,
  InvalidScopeError,
} from '../../../libs/errors/oauth.errors';
import {
  APP_WORKSPACE_MISMATCH_MESSAGE,
  isAppUsableInOrg,
} from './oauth.app.service';
import {
  OAuthTokenPayload,
  GeneratedTokens,
  KnowledgeLimit,
  GenerateTokensOptions,
  IntrospectResponse,
  TokenListItem,
} from '../types/oauth.types';
import { JwtConfig, getJwtKeyFromConfig } from '../../../libs/utils/jwtConfig';
import { stripTokenDisplayPrefix } from '../constants/constants';
import { parseScopeString } from './scope.validator.service';

/**
 * A grant is covered only when it asks for something and every requested
 * scope is still allowed. An empty list must not count: Array.every is
 * true for [], which would leave a scopeless token in place.
 */
export function isGrantCoveredBy(
  scopes: readonly string[],
  allowedScopes: readonly string[] | null | undefined,
): boolean {
  if (scopes.length === 0 || allowedScopes == null) {
    return false;
  }
  return scopes.every((scope) => allowedScopes.includes(scope));
}

/** What the personal-token views show; callers that need more pass their own. */
const DEFAULT_TOKEN_LIST_LIMIT = 100;

@injectable()
export class OAuthTokenService {
  private algorithm: Algorithm;
  private signingKey: Secret;
  private verifyKey: Secret;
  private keyId?: string;

  constructor(
    @inject('Logger') private logger: Logger,
    @inject('JwtConfig') private jwtConfig: JwtConfig,
    @inject('OAUTH_ISSUER') private issuer: string,
  ) {
    const keyConfig = getJwtKeyFromConfig(jwtConfig);
    this.algorithm = keyConfig.algorithm;
    this.signingKey = keyConfig.signingKey;
    this.verifyKey = keyConfig.verifyKey;
    this.keyId = keyConfig.keyId;
  }

  getAlgorithm(): Algorithm {
    return this.algorithm;
  }

  getKeyId(): string | undefined {
    return this.keyId;
  }

  getPublicKey(): string | undefined {
    return this.jwtConfig.publicKey;
  }

  /**
   * Generate access and refresh tokens
   */
  async generateTokens(
    app: OAuthAppDto,
    userId: string | null,
    orgId: string,
    scopes: string[],
    includeRefreshToken: boolean = true,
    fullName?: string,
    accountType?: string,
    opts?: GenerateTokensOptions,
  ): Promise<GeneratedTokens> {
    if (scopes.length === 0) {
      throw new InvalidScopeError('A token must carry at least one scope');
    }

    const jti = randomUUID();
    const now = Math.floor(Date.now() / 1000);
    // A personal access token names its own limit, and a user grant carries
    // the one its user agreed to. Every other token takes its app's.
    const knowledgeLimit =
      opts?.knowledgeLimit ??
      (opts?.grantedKnowledgeLimit === undefined
        ? toKnowledgeLimit(app.knowledgeLimit)
        : toKnowledgeLimit(opts.grantedKnowledgeLimit));
    const accessTokenLifetime =
      opts?.accessTokenLifetimeOverrideSeconds ?? app.accessTokenLifetime;

    // Generate access token
    const accessTokenPayload: OAuthTokenPayload = {
      userId: userId || app.clientId,
      orgId,
      iss: this.issuer,
      exp: now + accessTokenLifetime,
      iat: now,
      jti,
      scope: scopes.join(' '),
      client_id: app.clientId,
      tokenType: 'oauth',
      fullName,
      accountType,
      // The identity a client_credentials token acts as: the app's chosen one
      // when it has been pointed at a service account, its creator otherwise.
      // The Python services read this claim to decide whose documents a
      // request may reach, so it has to carry the answer rather than the
      // creator — baking the creator in and resolving it only in Node would
      // leave search and the connectors acting as the person while the Node
      // routes acted as the service account.
      //
      // Changing an app's identity revokes its outstanding tokens, so none
      // minted under the previous answer survives to be honoured.
      createdBy: app.tokenIdentityUserId ?? app.createdBy,
      knowledgeLimit,
    };

    // A delegated token speaks as the person named in `userId`, so every
    // claim that answers "who is this" has to agree on that person. Only
    // `act` dissents, and it is the one claim nothing authorises on.
    //
    // `createdBy` is overwritten rather than left as the app's identity
    // because what it carries by default — whichever administrator first
    // minted a service token for this org — is both wrong and dangerous here.
    // Today it is inert: Python substitutes it into `userId` only when
    // `userId === client_id` (`app/api/middlewares/auth.py:129`), which a
    // delegated token never satisfies. Leaving an administrator's id on a
    // token that acts as a member would make that guard the only thing
    // standing between delegation and silent privilege escalation.
    if (opts?.actorUserId !== undefined) {
      accessTokenPayload.act = { sub: opts.actorUserId };
      accessTokenPayload.createdBy = opts.actorUserId;
    }

    const signOptions: jwt.SignOptions = { algorithm: this.algorithm };
    if (this.keyId) {
      signOptions.keyid = this.keyId;
    }
    const accessToken = jwt.sign(
      accessTokenPayload,
      this.signingKey,
      signOptions,
    );

    // Store access token hash for revocation lookup
    const accessTokenHash = this.hashToken(accessToken);
    const storedAccessToken = await getOAuthTokenRepository().issueAccess({
      tokenHash: accessTokenHash,
      clientId: app.clientId,
      userId: userId || undefined,
      orgId,
      scopes,
      expiresAt: new Date((now + accessTokenLifetime) * 1000),
      name: opts?.name,
      actorUserId: opts?.actorUserId,
      knowledgeLimit,
    });

    const result: GeneratedTokens = {
      accessToken,
      jti,
      accessTokenId: storedAccessToken._id,
      tokenType: 'Bearer',
      expiresIn: accessTokenLifetime,
      scope: scopes.join(' '),
    };

    // Generate refresh token if requested and user is present.
    //
    // Never for a delegated token, whatever the caller asked for. Its whole
    // safety argument is that it lasts minutes, and a refresh token would
    // turn it into a standing right to be that person — renewable long after
    // the allow-list entry behind it was withdrawn. The check belongs here
    // rather than at the call site so no future caller can opt back into it.
    if (
      includeRefreshToken &&
      userId &&
      opts?.actorUserId === undefined &&
      scopes.includes('offline_access')
    ) {
      const refreshJti = randomUUID();
      const refreshTokenPayload: OAuthTokenPayload = {
        userId: userId,
        orgId,
        iss: this.issuer,
        exp: now + app.refreshTokenLifetime,
        iat: now,
        jti: refreshJti,
        scope: scopes.join(' '),
        client_id: app.clientId,
        tokenType: 'oauth',
        isRefreshToken: true,
        fullName,
        accountType,
        // Same resolved identity as the access token above.
        createdBy: app.tokenIdentityUserId ?? app.createdBy,
      };

      const refreshToken = jwt.sign(
        refreshTokenPayload,
        this.signingKey,
        signOptions,
      );

      // Store refresh token
      const refreshTokenHash = this.hashToken(refreshToken);
      await getOAuthTokenRepository().issueRefresh({
        tokenHash: refreshTokenHash,
        clientId: app.clientId,
        userId,
        orgId,
        scopes,
        expiresAt: new Date((now + app.refreshTokenLifetime) * 1000),
        // Stored as null when there is none, so a refresh can tell "no
        // limit" from a token issued before the limit was stored here.
        knowledgeLimit: knowledgeLimit ?? null,
      });

      result.refreshToken = refreshToken;
    }

    if (opts?.recheckAppScopes) {
      await this.revokeIfAppChanged(
        app.clientId,
        scopes,
        // A personal access token's own limit is not the app's to change.
        opts.knowledgeLimit === undefined ? { knowledgeLimit } : undefined,
        result,
      );
    }

    this.logger.info('OAuth tokens generated', {
      clientId: app.clientId,
      userId,
      scopes,
      hasRefreshToken: !!result.refreshToken,
    });

    return result;
  }

  /**
   * updateApp saves a reduced scope list or a narrowed knowledge limit and
   * then revokes the app's tokens. A grant that read the app before that save
   * can insert its tokens after the revocation. Reading the app again after
   * the insert closes the gap: if this read still sees the old values, the
   * save and its revocation come later and catch these tokens; if it sees the
   * new ones, they are revoked here. A limit that only widened keeps the
   * tokens, which reach no more than the user agreed to.
   */
  private async revokeIfAppChanged(
    clientId: string,
    scopes: string[],
    signed: { knowledgeLimit: KnowledgeLimit | undefined } | undefined,
    tokens: GeneratedTokens,
  ): Promise<void> {
    const current =
      await getOAuthAppRepository().findLiveAllowedScopes(clientId);
    if (
      current &&
      isGrantCoveredBy(scopes, current.allowedScopes) &&
      (signed === undefined ||
        isKnowledgeLimitWithin(signed.knowledgeLimit, current.knowledgeLimit))
    ) {
      return;
    }

    await getOAuthTokenRepository().revokeIssued(
      this.hashToken(tokens.accessToken),
      tokens.refreshToken ? this.hashToken(tokens.refreshToken) : undefined,
    );
    throw new InvalidGrantError(
      "The application's permissions changed during sign-in. Sign in again.",
    );
  }

  /**
   * Verify access token
   */
  /**
   * Revoke every live delegated token obtained by one service account.
   *
   * Delegated tokens are stored against the person they speak as, so the
   * ordinary "revoke everything this user holds" sweep cannot see them. They
   * expire in minutes on their own, but disabling or deleting a service
   * account is a decision that should take effect at once rather than after a
   * final few minutes of it still acting as people.
   */
  async revokeDelegatedTokensForActor(
    actorUserId: string,
    revokedBy?: string,
    reason = 'The service account behind this token was switched off',
  ): Promise<number> {
    return getOAuthTokenRepository().revokeDelegatedForActor(
      actorUserId,
      revokedBy,
      reason,
    );
  }

  async verifyAccessToken(token: string): Promise<OAuthTokenPayload> {
    try {
      // Personal access tokens and service tokens carry a display-only
      // prefix ahead of the underlying JWT so they're grep-able in
      // logs/files. Strip it before verifying/hashing — every other token
      // type never has a prefix, so this is a no-op for them.
      const rawToken = stripTokenDisplayPrefix(token);

      const payload = jwt.verify(rawToken, this.verifyKey, {
        algorithms: [this.algorithm],
      }) as OAuthTokenPayload;

      if (payload.isRefreshToken) {
        throw new InvalidTokenError('Invalid token type');
      }

      if (parseScopeString(payload.scope).length === 0) {
        throw new InvalidTokenError('Token carries no scopes');
      }

      // Check if token is revoked
      const tokenHash = this.hashToken(rawToken);
      const storedToken = await getIdentityLookup().resolveToken(tokenHash);

      if (!storedToken) {
        throw new InvalidTokenError('Token has been revoked');
      }

      // Best-effort recency tracking for the token list UI — never blocks
      // or fails the request it's riding on.
      this.touchLastUsed(storedToken).catch((err: unknown) => {
        this.logger.warn('Failed to update token lastUsedAt', {
          error: err instanceof Error ? err.message : 'Unknown error',
        });
      });

      return payload;
    } catch (error) {
      if (error instanceof jwt.TokenExpiredError) {
        throw new ExpiredTokenError('Access token has expired');
      }
      if (error instanceof jwt.JsonWebTokenError) {
        throw new InvalidTokenError('Invalid access token');
      }
      throw error;
    }
  }

  /**
   * Verify refresh token
   */
  async verifyRefreshToken(token: string): Promise<OAuthTokenPayload> {
    try {
      const payload = jwt.verify(token, this.verifyKey, {
        algorithms: [this.algorithm],
      }) as OAuthTokenPayload;

      if (!payload.isRefreshToken) {
        throw new InvalidTokenError('Invalid token type');
      }

      const tokenHash = this.hashToken(token);
      const storedToken = await getIdentityLookup().resolveRefresh(tokenHash);

      if (!storedToken) {
        throw new InvalidTokenError('Refresh token has been revoked');
      }

      return payload;
    } catch (error) {
      if (error instanceof jwt.TokenExpiredError) {
        throw new ExpiredTokenError('Refresh token has expired');
      }
      if (error instanceof jwt.JsonWebTokenError) {
        throw new InvalidTokenError('Invalid refresh token');
      }
      throw error;
    }
  }

  /**
   * Refresh tokens - rotates refresh token for security
   */
  async refreshTokens(
    app: OAuthAppDto,
    refreshToken: string,
    requestedScopes?: string[],
  ): Promise<GeneratedTokens> {
    const payload = await this.verifyRefreshToken(refreshToken);

    // Ends refresh tokens that an app got for another workspace before the check existed.
    if (!isAppUsableInOrg(app, payload.orgId)) {
      throw new InvalidGrantError(APP_WORKSPACE_MISMATCH_MESSAGE);
    }

    // Get stored refresh token
    const tokenHash = this.hashToken(refreshToken);
    const storedToken =
      await getOAuthTokenRepository().findRefreshByHash(tokenHash);

    if (!storedToken) {
      throw new InvalidTokenError('Refresh token not found');
    }

    if (storedToken.clientId !== app.clientId) {
      this.logger.warn('Refresh token presented by a different client', {
        issuedTo: storedToken.clientId,
        presentedBy: app.clientId,
        userId: storedToken.userId,
      });
      throw new InvalidGrantError(
        'Refresh token was not issued to this client',
      );
    }

    // Determine scopes - can only be reduced, not expanded
    let scopes = storedToken.scopes;
    if (requestedScopes && requestedScopes.length > 0) {
      // Filter to only include scopes that were in the original grant
      scopes = requestedScopes.filter((s) => storedToken.scopes.includes(s));
    }
    // Nor beyond what the app allows now; it may have lost scopes since the grant.
    scopes = scopes.filter((s) => app.allowedScopes.includes(s));
    if (scopes.length === 0) {
      // Before rotation: a refresh that no longer overlaps the app must not
      // burn the client's still-valid refresh token.
      throw new InvalidScopeError(
        'None of the granted scopes are still allowed for this application',
      );
    }

    // Revoke old refresh token (rotation), atomically: of two refreshes
    // presenting the same token only one rotates it; the other has lost the
    // token and must not be issued a second pair.
    if (!(await getOAuthTokenRepository().rotateRefresh(storedToken._id))) {
      throw new InvalidGrantError('Refresh token has already been used');
    }

    // Generate new tokens
    const newTokens = await this.generateTokens(
      app,
      payload.userId,
      payload.orgId,
      scopes,
      true, // Include new refresh token
      payload.fullName,
      payload.accountType,
      // The limit the user agreed to, not the app's: an app whose limit has
      // widened since reaches the new items once its user connects again.
      // A token stored without one takes the app's, which updateApp keeps
      // from widening under it.
      {
        recheckAppScopes: true,
        grantedKnowledgeLimit: storedToken.knowledgeLimit,
      },
    );

    this.logger.info('Tokens refreshed', {
      clientId: app.clientId,
      userId: payload.userId,
      rotationCount: storedToken.rotationCount + 1,
    });

    return newTokens;
  }

  /**
   * Revoke token
   * @see RFC 7009 - OAuth 2.0 Token Revocation
   * @param token The token to revoke
   * @param clientId The client_id making the request (for ownership verification)
   * @param tokenType Optional hint about the token type
   */
  async revokeToken(
    token: string,
    clientId: string,
    tokenType?: 'access_token' | 'refresh_token',
  ): Promise<boolean> {
    const tokenHash = this.hashToken(token);
    const repo = getOAuthTokenRepository();

    // RFC 7009: The authorization server validates...that the token was issued to the client
    // We include clientId in the query to ensure the token belongs to this client

    if (!tokenType || tokenType === 'access_token') {
      if (await repo.revokeAccessByHash(tokenHash, clientId)) {
        this.logger.info('Access token revoked', { clientId });
        return true;
      }
    }

    if (!tokenType || tokenType === 'refresh_token') {
      if (await repo.revokeRefreshByHash(tokenHash, clientId)) {
        this.logger.info('Refresh token revoked', { clientId });
        return true;
      }
    }

    return false;
  }

  /**
   * Revoke all tokens for an app
   */
  async revokeAllTokensForApp(clientId: string): Promise<void> {
    await getOAuthTokenRepository().revokeAllForClient(clientId);

    this.logger.info('All tokens revoked for app', { clientId });
  }

  /**
   * Revoke the app's refresh tokens stored before they kept the user's
   * knowledge limit. A refresh gives those the app's current limit, so they
   * cannot outlive a widening of it. Their access tokens keep the limit they
   * were signed with and run out on their own.
   */
  async revokeRefreshTokensWithoutKnowledgeLimit(
    clientId: string,
  ): Promise<void> {
    await getOAuthTokenRepository().revokeRefreshWithoutKnowledgeLimit(
      clientId,
    );

    this.logger.info('Refresh tokens without a stored limit revoked', {
      clientId,
    });
  }

  /**
   * Revoke all tokens for a user in an app
   */
  /**
   * Revoke every token held by a user, under every client.
   *
   * The per-client version below is for "this app no longer speaks for you".
   * This one is for "this identity is gone", where leaving a credential alive
   * because it was issued by a different client would defeat the point.
   */
  async revokeEveryTokenForUser(userId: string): Promise<void> {
    await getOAuthTokenRepository().revokeAllForUser(userId);
  }

  async revokeAllTokensForUser(
    clientId: string,
    userId: string,
  ): Promise<void> {
    await getOAuthTokenRepository().revokeAllForClientUser(clientId, userId);

    this.logger.info('All tokens revoked for user in app', {
      clientId,
      userId,
    });
  }

  /**
   * Token introspection (RFC 7662)
   * @see https://datatracker.ietf.org/doc/html/rfc7662
   * @param token The token to introspect
   * @param clientId The client_id making the request (for ownership verification)
   */
  async introspectToken(
    token: string,
    clientId: string,
  ): Promise<IntrospectResponse> {
    try {
      const payload = jwt.verify(token, this.verifyKey, {
        algorithms: [this.algorithm],
      }) as OAuthTokenPayload;
      if (parseScopeString(payload.scope).length === 0) {
        return { active: false };
      }
      const tokenHash = this.hashToken(token);

      // RFC 7662: Validate that the token was issued to the requesting client
      // Or the requesting client is authorized to introspect tokens (resource server)
      // For now, we only allow the token's client to introspect it
      if (payload.client_id !== clientId) {
        // Return inactive rather than error to prevent information disclosure
        return { active: false };
      }

      // Check revocation
      const storedToken = !payload.isRefreshToken
        ? await getIdentityLookup().resolveToken(tokenHash)
        : await getIdentityLookup().resolveRefresh(tokenHash);

      if (!storedToken) {
        return { active: false };
      }

      // Build introspection response per RFC 7662
      const response: IntrospectResponse = {
        active: true,
        scope: payload.scope,
        client_id: payload.client_id,
        token_type: !payload.isRefreshToken ? 'Bearer' : 'refresh_token',
        exp: payload.exp,
        iat: payload.iat,
        user_id: payload.userId,
        iss: payload.iss,
        jti: payload.jti,
      };

      // RFC 7662: Add username if available (for user tokens)
      if (storedToken.userId) {
        response.username = storedToken.userId;
      }

      return response;
    } catch {
      return { active: false };
    }
  }

  /**
   * List active tokens for an app
   */
  async listTokensForApp(clientId: string): Promise<TokenListItem[]> {
    const { access: accessTokens, refresh: refreshTokens } =
      await getOAuthTokenRepository().listLiveForClient(clientId, 100);

    const tokens: TokenListItem[] = [
      ...accessTokens.map((t) => ({
        id: t._id,
        tokenType: 'access' as const,
        userId: t.userId,
        scopes: t.scopes,
        createdAt: t.createdAt,
        expiresAt: t.expiresAt,
        isRevoked: t.isRevoked,
        name: t.name,
        lastUsedAt: t.lastUsedAt,
      })),
      ...refreshTokens.map((t) => ({
        id: t._id,
        tokenType: 'refresh' as const,
        userId: t.userId,
        scopes: t.scopes,
        createdAt: t.createdAt,
        expiresAt: t.expiresAt,
        isRevoked: t.isRevoked,
      })),
    ];

    return tokens.sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());
  }

  /**
   * List a single user's active access tokens for a client — the
   * per-user counterpart to {@link listTokensForApp}, used by the
   * personal access token list view.
   *
   * `limit` exists because the answer is read for different reasons. A person
   * glancing at their own tokens is well served by the most recent hundred;
   * an administrator looking at a service account is trying to find every
   * credential it holds in order to revoke them, and a token they cannot see
   * is one they cannot revoke.
   */
  /** How many active access tokens a user holds for a client. */
  async countActiveAccessTokensForUser(
    clientId: string,
    userId: string,
  ): Promise<number> {
    return getOAuthTokenRepository().countLiveAccessForUser(clientId, userId);
  }

  async listAccessTokensForUser(
    clientId: string,
    userId: string,
    limit: number = DEFAULT_TOKEN_LIST_LIMIT,
  ): Promise<TokenListItem[]> {
    const tokens = await getOAuthTokenRepository().listLiveAccessForUser(
      clientId,
      userId,
      limit,
    );

    // A caller that hits the ceiling is being shown less than it asked for,
    // and for a revocation screen that means credentials nobody can see to
    // revoke. Saying so is the difference between a cap and a silent one.
    if (tokens.length === limit) {
      this.logger.warn(
        'Token list reached its limit; older tokens are not shown',
        { clientId, userId, limit },
      );
    }

    return tokens.map((t) => ({
      id: t._id,
      tokenType: 'access' as const,
      userId: t.userId,
      scopes: t.scopes,
      createdAt: t.createdAt,
      expiresAt: t.expiresAt,
      isRevoked: t.isRevoked,
      name: t.name,
      lastUsedAt: t.lastUsedAt,
      knowledgeLimit: toKnowledgeLimit(t.knowledgeLimit),
    }));
  }

  /**
   * List a client's active access tokens with real pagination — unlike
   * {@link listTokensForApp}, which caps at a fixed 100 rows. Used for
   * org-admin PAT listing, where silently dropping tokens past the
   * hundredth would defeat the point of a complete-visibility view.
   */
  async listAccessTokensForClientPaginated(
    clientId: string,
    page: number,
    limit: number,
  ): Promise<{ tokens: TokenListItem[]; total: number }> {
    const { rows: tokens, total } =
      await getOAuthTokenRepository().listLiveAccessForClientPage(
        clientId,
        page,
        limit,
      );

    return {
      tokens: tokens.map((t) => ({
        id: t._id,
        tokenType: 'access' as const,
        userId: t.userId,
        scopes: t.scopes,
        createdAt: t.createdAt,
        expiresAt: t.expiresAt,
        isRevoked: t.isRevoked,
        name: t.name,
        lastUsedAt: t.lastUsedAt,
        knowledgeLimit: toKnowledgeLimit(t.knowledgeLimit),
      })),
      total,
    };
  }

  /**
   * Revoke a single access token by its document id, scoped to the owning
   * client and user so one user can't revoke another's token even if both
   * share a client (e.g. the shared per-org personal-access-token client).
   */
  async revokeAccessTokenById(
    id: string,
    clientId: string,
    userId: string,
    revokedBy: string,
    reason?: string,
  ): Promise<boolean> {
    const revoked = await getOAuthTokenRepository().revokeAccessById(
      id,
      clientId,
      userId,
      revokedBy,
      reason,
    );

    if (revoked) {
      this.logger.info('Access token revoked by id', { id, clientId, userId });
      return true;
    }
    return false;
  }

  /**
   * Revoke a single access token by its document id, scoped only to the
   * owning client — not a specific user. For admin-initiated revocation
   * (e.g. a departed employee's personal access token), where the caller
   * legitimately needs to revoke a token they don't own themselves.
   */
  async revokeAccessTokenByIdForClient(
    id: string,
    clientId: string,
    revokedBy: string,
    reason?: string,
  ): Promise<boolean> {
    const revoked = await getOAuthTokenRepository().revokeAccessByIdForClient(
      id,
      clientId,
      revokedBy,
      reason,
    );

    if (revoked) {
      this.logger.info('Access token revoked by id (admin)', {
        id,
        clientId,
        revokedBy,
      });
      return true;
    }
    return false;
  }

  /**
   * Throttled recency update — skips the write if the token was already
   * touched within the last 5 minutes, so a hot token doesn't generate a
   * write on every authenticated request.
   */
  private async touchLastUsed(
    token: Pick<AccessTokenDto, '_id' | 'lastUsedAt' | 'orgId'>,
  ): Promise<void> {
    const staleThresholdMs = 5 * 60 * 1000;
    if (
      token.lastUsedAt &&
      Date.now() - token.lastUsedAt.getTime() < staleThresholdMs
    ) {
      return;
    }
    await getOAuthTokenRepository().touchAccessLastUsed(token._id, new Date());
  }

  /**
   * Decode token without verification (for error handling)
   */
  decodeToken(token: string): OAuthTokenPayload | null {
    try {
      return jwt.decode(token) as OAuthTokenPayload | null;
    } catch {
      return null;
    }
  }

  /**
   * Hash token for storage
   */
  private hashToken(token: string): string {
    return crypto.createHash('sha256').update(token).digest('hex');
  }
}
