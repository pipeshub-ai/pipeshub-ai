// authtoken.service.ts
import { injectable } from 'inversify';
import { createPublicKey, createSecretKey, KeyObject } from 'node:crypto';
import { UnauthorizedError } from '../errors/http.errors';
import { Logger } from '../services/logger.service';
import { isUserActionScope } from '../enums/token-scopes.enum';
import { deriveUserActionSecret } from '../utils/jwtKeys';
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

function isSignatureMismatch(error: unknown): boolean {
  return (
    error instanceof jwt.JsonWebTokenError &&
    error.message === 'invalid signature'
  );
}

@injectable()
export class AuthTokenService {
  private readonly logger = Logger.getInstance();
  private readonly jwtSecret: string;
  private readonly scopedJwtSecret: string;
  private readonly jwtVerificationKey: KeyObject;
  private readonly scopedJwtVerificationKey: KeyObject;
  private readonly userActionVerificationKey: KeyObject;
  private readonly jwtAlgorithms: jwt.Algorithm[];
  private readonly scopedJwtAlgorithms: jwt.Algorithm[];

  constructor(jwtSecret: string, scopedJwtSecret: string) {
    this.jwtSecret = jwtSecret;
    this.scopedJwtSecret = scopedJwtSecret;
    this.jwtVerificationKey = toVerificationKey(jwtSecret);
    this.scopedJwtVerificationKey = toVerificationKey(scopedJwtSecret);
    // A PEM public key has no shared secret to derive a subkey from.
    this.userActionVerificationKey =
      this.scopedJwtVerificationKey.type === 'secret'
        ? toVerificationKey(deriveUserActionSecret(scopedJwtSecret))
        : this.scopedJwtVerificationKey;
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
    const keys = isUserActionScope(scope)
      ? [
          this.userActionVerificationKey,
          // Accepts user-held tokens signed with the raw key before the key split.
          // Remove once the longest of those has expired everywhere: the deploy of
          // this change plus REFRESH_TOKEN_EXPIRY / EMAIL_VERIFIED_TOKEN_EXPIRY
          // (both 30d by default).
          this.scopedJwtVerificationKey,
        ]
      : [this.scopedJwtVerificationKey];
    const decoded = this.verifyWithKeys(token, keys, this.scopedJwtAlgorithms);
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

  private verifyWithKeys(
    token: string,
    keys: KeyObject[],
    algorithms: jwt.Algorithm[],
  ): TokenPayload {
    let failure: unknown;
    for (const key of keys) {
      try {
        return jwt.verify(token, key, { algorithms }) as TokenPayload;
      } catch (error) {
        // A signature mismatch only means "not this key"; any other error came
        // from the key that matched and says why the token was refused.
        if (failure === undefined || !isSignatureMismatch(error)) {
          failure = error;
        }
      }
    }
    this.logger.error('Token verification failed', { error: failure });
    throw new UnauthorizedError('Invalid token');
  }
}
