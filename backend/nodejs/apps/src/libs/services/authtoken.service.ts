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
 *
 * The empty-secret check is load-bearing, not defensive tidiness. verify()
 * rejects a falsy secret with "secret or public key must be provided" *before*
 * it coerces the key, so an empty string used to fail closed. A KeyObject is
 * always truthy, so without this guard an empty secret sails past that check
 * and every token forged with an empty HMAC key verifies successfully.
 */
function toVerificationKey(secret: string): KeyObject {
  if (!secret) {
    throw new Error('JWT secret is not configured');
  }
  if (secret.includes('-----BEGIN')) {
    return createPublicKey(secret);
  }
  return createSecretKey(Buffer.from(secret));
}

/** Algorithms a key type can legitimately verify, so a token's own `alg`
 * header cannot steer verification onto a weaker scheme. */
function algorithmsFor(key: KeyObject): jwt.Algorithm[] {
  return key.type === 'secret'
    ? ['HS256', 'HS384', 'HS512']
    : [
        'RS256',
        'RS384',
        'RS512',
        'ES256',
        'ES384',
        'ES512',
        'PS256',
        'PS384',
        'PS512',
      ];
}

@injectable()
export class AuthTokenService {
  private readonly logger = Logger.getInstance();
  private readonly jwtSecret: string;
  private readonly scopedJwtSecret: string;
  private readonly jwtVerificationKey: KeyObject;
  private readonly scopedJwtVerificationKey: KeyObject;
  private readonly jwtAlgorithms: jwt.Algorithm[];
  private readonly scopedJwtAlgorithms: jwt.Algorithm[];

  constructor(jwtSecret: string, scopedJwtSecret: string) {
    this.jwtSecret = jwtSecret;
    this.scopedJwtSecret = scopedJwtSecret;
    this.jwtVerificationKey = toVerificationKey(jwtSecret);
    this.scopedJwtVerificationKey = toVerificationKey(scopedJwtSecret);
    this.jwtAlgorithms = algorithmsFor(this.jwtVerificationKey);
    this.scopedJwtAlgorithms = algorithmsFor(this.scopedJwtVerificationKey);
  }

  async verifyToken(token: string): Promise<TokenPayload> {
    try {
      const decoded = jwt.verify(token, this.jwtVerificationKey, {
        algorithms: this.jwtAlgorithms,
      }) as TokenPayload;

      return decoded;
    } catch (error) {
      this.logger.error('Token verification failed', { error });
      throw new UnauthorizedError('Invalid token');
    }
  }

  async verifyScopedToken(token: string, scope: string): Promise<TokenPayload> {
    let decoded: TokenPayload;
    try {
      decoded = jwt.verify(token, this.scopedJwtVerificationKey, {
        algorithms: this.scopedJwtAlgorithms,
      }) as TokenPayload;
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

  generateToken(
    payload: TokenPayload,
    expiresIn: SignOptions['expiresIn'] = '7d',
  ): string {
    return jwt.sign(payload, this.jwtSecret, { expiresIn } as SignOptions);
  }

  generateScopedToken(
    payload: TokenPayload,
    expiresIn: SignOptions['expiresIn'] = '1h',
  ): string {
    return jwt.sign(payload, this.scopedJwtSecret, {
      expiresIn,
    } as SignOptions);
  }
}
