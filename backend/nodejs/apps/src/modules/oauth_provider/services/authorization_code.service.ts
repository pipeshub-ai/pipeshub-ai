import { injectable, inject } from 'inversify';
import crypto from 'crypto';
import { Logger } from '../../../libs/services/logger.service';
import { getOAuthTokenRepository } from '../../../libs/repositories/oauth-token';
import { InvalidGrantError } from '../../../libs/errors/oauth.errors';
import { AuthCodeExchangeResult, KnowledgeLimit } from '../types/oauth.types';
import { toKnowledgeLimit } from '../types/knowledge_limit';

const CODE_LENGTH = 32;
const CODE_EXPIRY_SECONDS = 600; // 10 minutes

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
    codeChallengeMethod?: 'S256',
    // What the user was shown at consent, so an exchange after an edit is refused.
    knowledgeLimit?: KnowledgeLimit,
  ): Promise<string> {
    const code = crypto.randomBytes(CODE_LENGTH).toString('hex');
    const expiresAt = new Date(Date.now() + CODE_EXPIRY_SECONDS * 1000);

    await getOAuthTokenRepository().createCode({
      code,
      clientId,
      userId,
      orgId,
      redirectUri,
      scopes,
      knowledgeLimit,
      expiresAt,
      codeChallenge,
      codeChallengeMethod,
    });

    this.logger.info('Authorization code generated', {
      clientId,
      userId,
      scopes,
      hasPKCE: !!codeChallenge,
    });

    return code;
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
    // Defaults to the strict (public client) path so a caller that forgets to
    // pass it fails closed rather than skipping PKCE.
    isConfidentialClient: boolean = false,
  ): Promise<AuthCodeExchangeResult> {
    // First, look for the code regardless of isUsed status
    const repo = getOAuthTokenRepository();
    const authCode = await repo.findCode(code, clientId);

    if (!authCode) {
      throw new InvalidGrantError('Invalid or expired authorization code');
    }

    // RFC 6749 Section 4.1.2: If code was already used, this is code reuse attack
    // MUST revoke all tokens previously issued based on that code
    if (authCode.isUsed) {
      await this.rejectReplay(code, clientId, authCode);
    }

    if (authCode.expiresAt < new Date()) {
      // Clean up expired code
      await repo.deleteCode(authCode._id);
      throw new InvalidGrantError('Authorization code has expired');
    }

    if (authCode.redirectUri !== redirectUri) {
      throw new InvalidGrantError('Redirect URI mismatch');
    }

    // RFC 9700: public clients have no other proof of possession, so a code
    // stored without a challenge must never be redeemable by one.
    if (!authCode.codeChallenge && !isConfidentialClient) {
      throw new InvalidGrantError('PKCE is required for public clients');
    }

    // PKCE verification
    if (authCode.codeChallenge) {
      if (!codeVerifier) {
        throw new InvalidGrantError('Code verifier required for PKCE');
      }

      // Validate code_verifier format per RFC 7636
      if (!AuthorizationCodeService.validateCodeVerifier(codeVerifier)) {
        throw new InvalidGrantError('Invalid code_verifier format');
      }

      const isValid = this.verifyCodeChallenge(
        codeVerifier,
        authCode.codeChallenge,
        authCode.codeChallengeMethod || 'S256',
      );

      if (!isValid) {
        throw new InvalidGrantError('Invalid code verifier');
      }
    }

    // One-time use, decided by a single conditional write: of two exchanges
    // racing on the same code exactly one gets here. The other is a replay.
    if (!(await repo.consumeCode(code, clientId))) {
      // Lost the write: a replay only if the code really was used. It may also
      // have expired or been swept since it was validated, which revokes nothing.
      const now = await repo.findCode(code, clientId);
      if (now?.isUsed) {
        await this.rejectReplay(code, clientId, authCode);
      }
      throw new InvalidGrantError(
        now
          ? 'Authorization code has expired'
          : 'Invalid or expired authorization code',
      );
    }

    this.logger.info('Authorization code exchanged', {
      clientId,
      userId: authCode.userId,
    });

    return {
      userId: authCode.userId,
      orgId: authCode.orgId,
      scopes: authCode.scopes,
      knowledgeLimit: toKnowledgeLimit(authCode.knowledgeLimit),
    };
  }

  /** A used code presented again: revoke what it issued (RFC 6749 4.1.2) and refuse. */
  private async rejectReplay(
    code: string,
    clientId: string,
    authCode: { _id: string; userId: string },
  ): Promise<never> {
    this.logger.warn(
      'Authorization code reuse detected! Revoking all associated tokens',
      {
        clientId,
        userId: authCode.userId,
        codeId: authCode._id,
      },
    );
    // Flag first, revoke second; the winner mints, then reads the flag. Either it
    // sees the flag and revokes its own pair, or it minted before the flag, and so
    // before the revocation below, which then covers it.
    await getOAuthTokenRepository().flagCodeReplayed(code, clientId);
    await this.revokeTokensForCodeReuse(clientId, authCode.userId);
    throw new InvalidGrantError('Authorization code has already been used');
  }

  /** True when the code was replayed while this redeemer was minting its tokens. */
  async wasReplayed(code: string, clientId: string): Promise<boolean> {
    const row = await getOAuthTokenRepository().findCode(code, clientId);
    return row?.replayedAt != null;
  }

  /**
   * Revoke all tokens for a client/user when code reuse is detected
   * @see RFC 6749 Section 4.1.2 - "SHOULD revoke...all tokens previously issued"
   */
  private async revokeTokensForCodeReuse(
    clientId: string,
    userId: string,
  ): Promise<void> {
    await getOAuthTokenRepository().revokeAllForClientUserBecause(
      clientId,
      userId,
      'code_reuse_detected',
    );

    this.logger.warn('All tokens revoked due to code reuse', {
      clientId,
      userId,
    });
  }

  /**
   * Clean up expired authorization codes
   */
  async cleanupExpiredCodes(): Promise<number> {
    const deleted = await getOAuthTokenRepository().deleteExpiredCodes();

    if (deleted > 0) {
      this.logger.info('Cleaned up expired authorization codes', {
        count: deleted,
      });
    }

    return deleted;
  }

  /**
   * Revoke all codes for a user
   */
  async revokeCodesForUser(userId: string): Promise<void> {
    await getOAuthTokenRepository().deleteUnusedCodesForUser(userId);

    this.logger.info('Revoked authorization codes for user', { userId });
  }

  /**
   * Revoke all codes for an app
   */
  async revokeCodesForApp(clientId: string): Promise<void> {
    await getOAuthTokenRepository().deleteUnusedCodesForClient(clientId);

    this.logger.info('Revoked authorization codes for app', { clientId });
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
      return verifier === challenge;
    }

    // S256: BASE64URL(SHA256(code_verifier)) == code_challenge
    const hash = crypto
      .createHash('sha256')
      .update(verifier)
      .digest('base64')
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=/g, '');

    return hash === challenge;
  }

  /**
   * Validate PKCE code verifier format
   */
  static validateCodeVerifier(verifier: string): boolean {
    // Code verifier must be 43-128 characters
    // Only allowed characters: [A-Z] / [a-z] / [0-9] / "-" / "." / "_" / "~"
    if (verifier.length < 43 || verifier.length > 128) {
      return false;
    }

    const validPattern = /^[A-Za-z0-9\-._~]+$/;
    return validPattern.test(verifier);
  }

  /**
   * Validate PKCE code challenge format
   */
  static validateCodeChallenge(challenge: string): boolean {
    // Code challenge must be 43-128 characters (base64url encoded)
    if (challenge.length < 43 || challenge.length > 128) {
      return false;
    }

    const validPattern = /^[A-Za-z0-9\-_]+$/;
    return validPattern.test(challenge);
  }
}
