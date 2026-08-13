// authtoken.service.ts
import { injectable } from 'inversify';
import { createPublicKey, createSecretKey, KeyObject } from 'node:crypto';
import { UnauthorizedError } from '../errors/http.errors';
import { Logger } from '../services/logger.service';
import jwt, { SignOptions } from 'jsonwebtoken';

interface TokenPayload extends Record<string, any> {}

/**
 * Turn a configured secret into a KeyObject once, at construction.
 *
 * Given a raw string, jsonwebtoken's verify() calls createPublicKey() on it for
 * every single call. Our tokens are HS256 (generateToken signs without an
 * algorithm option, so jsonwebtoken defaults to HS256), which means that parse
 * can never succeed: OpenSSL tries the string as PEM/DER, fails, throws, and
 * verify() falls back to createSecretKey. Profiling the gateway under load put
 * that failed-parse path at ~20% of its total CPU.
 *
 * Passing a KeyObject skips the try/throw entirely. PEM input is still handled
 * so an asymmetric deployment keeps working.
 */
function toVerificationKey(secret: string): KeyObject {
  if (secret.includes('-----BEGIN')) {
    return createPublicKey(secret);
  }
  return createSecretKey(Buffer.from(secret));
}

@injectable()
export class AuthTokenService {
  private readonly logger = Logger.getInstance();
  private readonly jwtSecret: string;
  private readonly scopedJwtSecret: string;
  private readonly jwtVerificationKey: KeyObject;
  private readonly scopedJwtVerificationKey: KeyObject;

  constructor(jwtSecret: string, scopedJwtSecret: string) {
    this.jwtSecret = jwtSecret;
    this.scopedJwtSecret = scopedJwtSecret;
    this.jwtVerificationKey = toVerificationKey(jwtSecret);
    this.scopedJwtVerificationKey = toVerificationKey(scopedJwtSecret);
  }

  async verifyToken(token: string): Promise<TokenPayload> {
    try {
      const decoded = jwt.verify(
        token,
        this.jwtVerificationKey,
      ) as TokenPayload;

      return decoded;
    } catch (error) {
      this.logger.error('Token verification failed', { error });
      throw new UnauthorizedError('Invalid token');
    }
  }

  async verifyScopedToken(token: string, scope: string): Promise<TokenPayload> {
    let decoded: TokenPayload;
    try {
      decoded = jwt.verify(
        token,
        this.scopedJwtVerificationKey,
      ) as TokenPayload;
    } catch (error) {
      this.logger.error('Token verification failed', { error });
      throw new UnauthorizedError('Invalid token');
    }
    const { scopes } = decoded;
    if (!scopes || !scopes.includes(scope)) {
      throw new UnauthorizedError('Invalid scope');
    }

    return decoded;
  }

  generateToken(payload: TokenPayload, expiresIn: SignOptions['expiresIn'] = '7d'): string {
    return jwt.sign(payload, this.jwtSecret, { expiresIn } as SignOptions);
  }

  generateScopedToken(payload: TokenPayload, expiresIn: SignOptions['expiresIn'] = '1h'): string {
    return jwt.sign(payload, this.scopedJwtSecret, { expiresIn } as SignOptions);
  }
}
