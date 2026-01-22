import { injectable, inject } from 'inversify'
import crypto from 'crypto'
import { Types } from 'mongoose'
import { Logger } from '../../../libs/services/logger.service'
import { AuthorizationCode } from '../schema/authorization_code.schema'
import { OAuthAccessToken } from '../schema/oauth.access_token.schema'
import { OAuthRefreshToken } from '../schema/oauth.refresh_token.schema'
import { InvalidGrantError } from '../../../libs/errors/oauth.errors'
import { AuthCodeExchangeResult } from '../types/oauth.types'

const CODE_LENGTH = 32
const CODE_EXPIRY_SECONDS = 600 // 10 minutes

@injectable()
export class AuthorizationCodeService {
  constructor(@inject('Logger') private logger: Logger) {}

  /**
   * Generate authorization code
   */
  async generateCode(
    clientId: string,
    userId: string,
    orgId: string,
    redirectUri: string,
    scopes: string[],
    codeChallenge?: string,
    codeChallengeMethod?: 'S256' | 'plain',
  ): Promise<string> {
    const code = crypto.randomBytes(CODE_LENGTH).toString('hex')
    const expiresAt = new Date(Date.now() + CODE_EXPIRY_SECONDS * 1000)

    await AuthorizationCode.create({
      code,
      clientId,
      userId: new Types.ObjectId(userId),
      orgId: new Types.ObjectId(orgId),
      redirectUri,
      scopes,
      expiresAt,
      codeChallenge,
      codeChallengeMethod,
    })

    this.logger.info('Authorization code generated', {
      clientId,
      userId,
      scopes,
      hasPKCE: !!codeChallenge,
    })

    return code
  }

  /**
   * Exchange authorization code for tokens
   * @see RFC 6749 Section 4.1.2 - Authorization Code exchange
   */
  async exchangeCode(
    code: string,
    clientId: string,
    redirectUri: string,
    codeVerifier?: string,
  ): Promise<AuthCodeExchangeResult> {
    // First, look for the code regardless of isUsed status
    const authCode = await AuthorizationCode.findOne({
      code: { $eq: code },
      clientId: { $eq: clientId },
    })

    if (!authCode) {
      throw new InvalidGrantError('Invalid or expired authorization code')
    }

    // RFC 6749 Section 4.1.2: If code was already used, this is code reuse attack
    // MUST revoke all tokens previously issued based on that code
    if (authCode.isUsed) {
      this.logger.warn('Authorization code reuse detected! Revoking all associated tokens', {
        clientId,
        userId: authCode.userId.toString(),
        codeId: (authCode._id as Types.ObjectId).toString(),
      })

      // Revoke all tokens issued for this user/client combination
      // This is a security measure per RFC 6749
      await this.revokeTokensForCodeReuse(clientId, authCode.userId.toString())

      throw new InvalidGrantError('Authorization code has already been used')
    }

    if (authCode.expiresAt < new Date()) {
      // Clean up expired code
      await AuthorizationCode.deleteOne({ _id: authCode._id })
      throw new InvalidGrantError('Authorization code has expired')
    }

    if (authCode.redirectUri !== redirectUri) {
      throw new InvalidGrantError('Redirect URI mismatch')
    }

    // PKCE verification
    if (authCode.codeChallenge) {
      if (!codeVerifier) {
        throw new InvalidGrantError('Code verifier required for PKCE')
      }

      // Validate code_verifier format per RFC 7636
      if (!AuthorizationCodeService.validateCodeVerifier(codeVerifier)) {
        throw new InvalidGrantError('Invalid code_verifier format')
      }

      const isValid = this.verifyCodeChallenge(
        codeVerifier,
        authCode.codeChallenge,
        authCode.codeChallengeMethod || 'S256',
      )

      if (!isValid) {
        throw new InvalidGrantError('Invalid code verifier')
      }
    }

    // Mark code as used (one-time use)
    authCode.isUsed = true
    authCode.usedAt = new Date()
    await authCode.save()

    this.logger.info('Authorization code exchanged', {
      clientId,
      userId: authCode.userId.toString(),
    })

    return {
      userId: authCode.userId.toString(),
      orgId: authCode.orgId.toString(),
      scopes: authCode.scopes,
    }
  }

  /**
   * Revoke all tokens for a client/user when code reuse is detected
   * @see RFC 6749 Section 4.1.2 - "SHOULD revoke...all tokens previously issued"
   */
  private async revokeTokensForCodeReuse(
    clientId: string,
    userId: string,
  ): Promise<void> {
    const userObjId = new Types.ObjectId(userId)

    await Promise.all([
      OAuthAccessToken.updateMany(
        { clientId: { $eq: clientId }, userId: userObjId, isRevoked: false },
        { isRevoked: true, revokedAt: new Date(), revokedReason: 'code_reuse_detected' },
      ),
      OAuthRefreshToken.updateMany(
        { clientId: { $eq: clientId }, userId: userObjId, isRevoked: false },
        { isRevoked: true, revokedAt: new Date(), revokedReason: 'code_reuse_detected' },
      ),
    ])

    this.logger.warn('All tokens revoked due to code reuse', { clientId, userId })
  }

  /**
   * Clean up expired authorization codes
   */
  async cleanupExpiredCodes(): Promise<number> {
    const result = await AuthorizationCode.deleteMany({
      $or: [{ expiresAt: { $lt: new Date() } }, { isUsed: true }],
    })

    if (result.deletedCount > 0) {
      this.logger.info('Cleaned up expired authorization codes', {
        count: result.deletedCount,
      })
    }

    return result.deletedCount
  }

  /**
   * Revoke all codes for a user
   */
  async revokeCodesForUser(userId: string): Promise<void> {
    await AuthorizationCode.deleteMany({
      userId: new Types.ObjectId(userId),
      isUsed: false,
    })

    this.logger.info('Revoked authorization codes for user', { userId })
  }

  /**
   * Revoke all codes for an app
   */
  async revokeCodesForApp(clientId: string): Promise<void> {
    await AuthorizationCode.deleteMany({
      clientId: { $eq: clientId },
      isUsed: false,
    })

    this.logger.info('Revoked authorization codes for app', { clientId })
  }

  /**
   * Verify PKCE code challenge
   */
  private verifyCodeChallenge(
    verifier: string,
    challenge: string,
    method: 'S256' | 'plain',
  ): boolean {
    if (method === 'plain') {
      return verifier === challenge
    }

    // S256: BASE64URL(SHA256(code_verifier)) == code_challenge
    const hash = crypto
      .createHash('sha256')
      .update(verifier)
      .digest('base64')
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=/g, '')

    return hash === challenge
  }

  /**
   * Validate PKCE code verifier format
   */
  static validateCodeVerifier(verifier: string): boolean {
    // Code verifier must be 43-128 characters
    // Only allowed characters: [A-Z] / [a-z] / [0-9] / "-" / "." / "_" / "~"
    if (verifier.length < 43 || verifier.length > 128) {
      return false
    }

    const validPattern = /^[A-Za-z0-9\-._~]+$/
    return validPattern.test(verifier)
  }

  /**
   * Validate PKCE code challenge format
   */
  static validateCodeChallenge(challenge: string): boolean {
    // Code challenge must be 43-128 characters (base64url encoded)
    if (challenge.length < 43 || challenge.length > 128) {
      return false
    }

    const validPattern = /^[A-Za-z0-9\-_]+$/
    return validPattern.test(challenge)
  }
}
