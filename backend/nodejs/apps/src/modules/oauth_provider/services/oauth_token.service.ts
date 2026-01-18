import { injectable, inject } from 'inversify'
import jwt from 'jsonwebtoken'
import crypto from 'crypto'
import { v4 as uuidv4 } from 'uuid'
import { Types } from 'mongoose'
import { Logger } from '../../../libs/services/logger.service'
import { OAuthAccessToken } from '../schema/oauth.access_token.schema'
import { OAuthRefreshToken } from '../schema/oauth.refresh_token.schema'
import { IOAuthApp } from '../schema/oauth.app.schema'
import {
  InvalidTokenError,
  ExpiredTokenError,
} from '../../../libs/errors/oauth.errors'
import {
  OAuthTokenPayload,
  GeneratedTokens,
  IntrospectResponse,
  TokenListItem,
} from '../types/oauth.types'
import { RSAKeyService } from './rsa_key.service'

@injectable()
export class OAuthTokenService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('RSAKeyService') private rsaKeyService: RSAKeyService,
    @inject('OAUTH_ISSUER') private issuer: string,
  ) {}

  /**
   * Generate access and refresh tokens
   */
  async generateTokens(
    app: IOAuthApp,
    userId: string | null,
    orgId: string,
    scopes: string[],
    includeRefreshToken: boolean = true,
  ): Promise<GeneratedTokens> {
    const jti = uuidv4()
    const now = Math.floor(Date.now() / 1000)

    // Generate access token
    const accessTokenPayload: OAuthTokenPayload = {
      sub: userId || app.clientId,
      iss: this.issuer,
      aud: app.clientId,
      exp: now + app.accessTokenLifetime,
      iat: now,
      jti,
      scope: scopes.join(' '),
      client_id: app.clientId,
      org_id: orgId,
      token_type: 'access',
    }

    const accessToken = jwt.sign(accessTokenPayload, this.rsaKeyService.getPrivateKey(), {
      algorithm: 'RS256',
      keyid: this.rsaKeyService.getKeyId(),
    })

    // Store access token hash for revocation lookup
    const accessTokenHash = this.hashToken(accessToken)
    await OAuthAccessToken.create({
      tokenHash: accessTokenHash,
      clientId: app.clientId,
      userId: userId ? new Types.ObjectId(userId) : undefined,
      orgId: new Types.ObjectId(orgId),
      scopes,
      expiresAt: new Date((now + app.accessTokenLifetime) * 1000),
    })

    const result: GeneratedTokens = {
      accessToken,
      tokenType: 'Bearer',
      expiresIn: app.accessTokenLifetime,
      scope: scopes.join(' '),
    }

    // Generate refresh token if requested and user is present
    if (includeRefreshToken && userId && scopes.includes('offline_access')) {
      const refreshJti = uuidv4()
      const refreshTokenPayload: OAuthTokenPayload = {
        sub: userId,
        iss: this.issuer,
        aud: app.clientId,
        exp: now + app.refreshTokenLifetime,
        iat: now,
        jti: refreshJti,
        scope: scopes.join(' '),
        client_id: app.clientId,
        org_id: orgId,
        token_type: 'refresh',
      }

      const refreshToken = jwt.sign(refreshTokenPayload, this.rsaKeyService.getPrivateKey(), {
        algorithm: 'RS256',
        keyid: this.rsaKeyService.getKeyId(),
      })

      // Store refresh token
      const refreshTokenHash = this.hashToken(refreshToken)
      await OAuthRefreshToken.create({
        tokenHash: refreshTokenHash,
        clientId: app.clientId,
        userId: new Types.ObjectId(userId),
        orgId: new Types.ObjectId(orgId),
        scopes,
        expiresAt: new Date((now + app.refreshTokenLifetime) * 1000),
      })

      result.refreshToken = refreshToken
    }

    this.logger.info('OAuth tokens generated', {
      clientId: app.clientId,
      userId,
      scopes,
      hasRefreshToken: !!result.refreshToken,
    })

    return result
  }

  /**
   * Verify access token
   */
  async verifyAccessToken(token: string): Promise<OAuthTokenPayload> {
    try {
      const payload = jwt.verify(token, this.rsaKeyService.getPublicKey(), {
        algorithms: ['RS256'],
      }) as OAuthTokenPayload

      if (payload.token_type !== 'access') {
        throw new InvalidTokenError('Invalid token type')
      }

      // Check if token is revoked
      const tokenHash = this.hashToken(token)
      const storedToken = await OAuthAccessToken.findOne({
        tokenHash: { $eq: tokenHash },
        isRevoked: { $eq: false },
      })

      if (!storedToken) {
        throw new InvalidTokenError('Token has been revoked')
      }

      return payload
    } catch (error) {
      if (error instanceof jwt.TokenExpiredError) {
        throw new ExpiredTokenError('Access token has expired')
      }
      if (error instanceof jwt.JsonWebTokenError) {
        throw new InvalidTokenError('Invalid access token')
      }
      throw error
    }
  }

  /**
   * Verify refresh token
   */
  async verifyRefreshToken(token: string): Promise<OAuthTokenPayload> {
    try {
      const payload = jwt.verify(token, this.rsaKeyService.getPublicKey(), {
        algorithms: ['RS256'],
      }) as OAuthTokenPayload

      if (payload.token_type !== 'refresh') {
        throw new InvalidTokenError('Invalid token type')
      }

      const tokenHash = this.hashToken(token)
      const storedToken = await OAuthRefreshToken.findOne({
        tokenHash: { $eq: tokenHash },
        isRevoked: { $eq: false },
      })

      if (!storedToken) {
        throw new InvalidTokenError('Refresh token has been revoked')
      }

      return payload
    } catch (error) {
      if (error instanceof jwt.TokenExpiredError) {
        throw new ExpiredTokenError('Refresh token has expired')
      }
      if (error instanceof jwt.JsonWebTokenError) {
        throw new InvalidTokenError('Invalid refresh token')
      }
      throw error
    }
  }

  /**
   * Refresh tokens - rotates refresh token for security
   */
  async refreshTokens(
    app: IOAuthApp,
    refreshToken: string,
    requestedScopes?: string[],
  ): Promise<GeneratedTokens> {
    const payload = await this.verifyRefreshToken(refreshToken)

    // Get stored refresh token
    const tokenHash = this.hashToken(refreshToken)
    const storedToken = await OAuthRefreshToken.findOne({ tokenHash: { $eq: tokenHash } })

    if (!storedToken) {
      throw new InvalidTokenError('Refresh token not found')
    }

    // Determine scopes - can only be reduced, not expanded
    let scopes = storedToken.scopes
    if (requestedScopes && requestedScopes.length > 0) {
      // Filter to only include scopes that were in the original grant
      scopes = requestedScopes.filter((s) => storedToken.scopes.includes(s))
    }

    // Revoke old refresh token (rotation)
    storedToken.isRevoked = true
    storedToken.revokedAt = new Date()
    await storedToken.save()

    // Generate new tokens
    const newTokens = await this.generateTokens(
      app,
      payload.sub,
      payload.org_id,
      scopes,
      true, // Include new refresh token
    )

    this.logger.info('Tokens refreshed', {
      clientId: app.clientId,
      userId: payload.sub,
      rotationCount: storedToken.rotationCount + 1,
    })

    return newTokens
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
    const tokenHash = this.hashToken(token)

    // RFC 7009: The authorization server validates...that the token was issued to the client
    // We include clientId in the query to ensure the token belongs to this client

    if (!tokenType || tokenType === 'access_token') {
      const result = await OAuthAccessToken.updateOne(
        {
          tokenHash: { $eq: tokenHash },
          clientId: { $eq: clientId },
          isRevoked: { $eq: false },
        },
        { isRevoked: true, revokedAt: new Date() },
      )
      if (result.modifiedCount > 0) {
        this.logger.info('Access token revoked', { clientId })
        return true
      }
    }

    if (!tokenType || tokenType === 'refresh_token') {
      const result = await OAuthRefreshToken.updateOne(
        {
          tokenHash: { $eq: tokenHash },
          clientId: { $eq: clientId },
          isRevoked: { $eq: false },
        },
        { isRevoked: true, revokedAt: new Date() },
      )
      if (result.modifiedCount > 0) {
        this.logger.info('Refresh token revoked', { clientId })
        return true
      }
    }

    return false
  }

  /**
   * Revoke all tokens for an app
   */
  async revokeAllTokensForApp(clientId: string): Promise<void> {
    await Promise.all([
      OAuthAccessToken.updateMany(
        { clientId: { $eq: clientId }, isRevoked: { $eq: false } },
        { isRevoked: true, revokedAt: new Date() },
      ),
      OAuthRefreshToken.updateMany(
        { clientId: { $eq: clientId }, isRevoked: { $eq: false } },
        { isRevoked: true, revokedAt: new Date() },
      ),
    ])

    this.logger.info('All tokens revoked for app', { clientId })
  }

  /**
   * Revoke all tokens for a user in an app
   */
  async revokeAllTokensForUser(
    clientId: string,
    userId: string,
  ): Promise<void> {
    const userObjId = new Types.ObjectId(userId)
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
    ])

    this.logger.info('All tokens revoked for user in app', { clientId, userId })
  }

  /**
   * Token introspection (RFC 7662)
   * @see https://datatracker.ietf.org/doc/html/rfc7662
   * @param token The token to introspect
   * @param clientId The client_id making the request (for ownership verification)
   */
  async introspectToken(token: string, clientId: string): Promise<IntrospectResponse> {
    try {
      const payload = jwt.verify(token, this.rsaKeyService.getPublicKey(), {
        algorithms: ['RS256'],
      }) as OAuthTokenPayload
      const tokenHash = this.hashToken(token)

      // RFC 7662: Validate that the token was issued to the requesting client
      // Or the requesting client is authorized to introspect tokens (resource server)
      // For now, we only allow the token's client to introspect it
      if (payload.client_id !== clientId) {
        // Return inactive rather than error to prevent information disclosure
        return { active: false }
      }

      // Check revocation
      const storedToken =
        payload.token_type === 'access'
          ? await OAuthAccessToken.findOne({
              tokenHash: { $eq: tokenHash },
              isRevoked: { $eq: false },
            })
          : await OAuthRefreshToken.findOne({
              tokenHash: { $eq: tokenHash },
              isRevoked: { $eq: false },
            })

      if (!storedToken) {
        return { active: false }
      }

      // Build introspection response per RFC 7662
      const response: IntrospectResponse = {
        active: true,
        scope: payload.scope,
        client_id: payload.client_id,
        token_type: payload.token_type === 'access' ? 'Bearer' : 'refresh_token',
        exp: payload.exp,
        iat: payload.iat,
        sub: payload.sub,
        aud: payload.aud,
        iss: payload.iss,
        jti: payload.jti,
      }

      // RFC 7662: Add username if available (for user tokens)
      if (storedToken.userId) {
        response.username = storedToken.userId.toString()
      }

      return response
    } catch {
      return { active: false }
    }
  }

  /**
   * List active tokens for an app
   */
  async listTokensForApp(clientId: string): Promise<TokenListItem[]> {
    const [accessTokens, refreshTokens] = await Promise.all([
      OAuthAccessToken.find({
        clientId: { $eq: clientId },
        isRevoked: { $eq: false },
        expiresAt: { $gt: new Date() },
      })
        .sort({ createdAt: -1 })
        .limit(100)
        .exec(),
      OAuthRefreshToken.find({
        clientId: { $eq: clientId },
        isRevoked: { $eq: false },
        expiresAt: { $gt: new Date() },
      })
        .sort({ createdAt: -1 })
        .limit(100)
        .exec(),
    ])

    const tokens: TokenListItem[] = [
      ...accessTokens.map((t) => ({
        id: (t._id as Types.ObjectId).toString(),
        tokenType: 'access' as const,
        userId: t.userId?.toString(),
        scopes: t.scopes,
        createdAt: t.createdAt,
        expiresAt: t.expiresAt,
        isRevoked: t.isRevoked,
      })),
      ...refreshTokens.map((t) => ({
        id: (t._id as Types.ObjectId).toString(),
        tokenType: 'refresh' as const,
        userId: t.userId.toString(),
        scopes: t.scopes,
        createdAt: t.createdAt,
        expiresAt: t.expiresAt,
        isRevoked: t.isRevoked,
      })),
    ]

    return tokens.sort(
      (a, b) => b.createdAt.getTime() - a.createdAt.getTime(),
    )
  }

  /**
   * Decode token without verification (for error handling)
   */
  decodeToken(token: string): OAuthTokenPayload | null {
    try {
      return jwt.decode(token) as OAuthTokenPayload | null
    } catch {
      return null
    }
  }

  /**
   * Hash token for storage
   */
  private hashToken(token: string): string {
    return crypto.createHash('sha256').update(token).digest('hex')
  }
}
