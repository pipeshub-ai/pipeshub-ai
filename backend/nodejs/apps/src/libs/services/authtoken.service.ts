// authtoken.service.ts
import { injectable } from 'inversify';
import { UnauthorizedError } from '../errors/http.errors';
import { Logger } from '../services/logger.service';
import jwt from 'jsonwebtoken';

interface TokenPayload extends Record<string, any> {}

@injectable()
export class AuthTokenService {
  private readonly logger = Logger.getInstance();
  private readonly algorithm: jwt.Algorithm;
  private readonly jwtKey: string;
  private readonly jwtVerifyKey: string;
  private readonly scopedJwtKey: string;
  private readonly scopedJwtVerifyKey: string;

  constructor(
    algorithm: 'HS256' | 'RS256',
    jwtSecret?: string,
    jwtPrivateKey?: string, 
    jwtPublicKey?: string,
    scopedJwtSecret?: string,
    scopedJwtPrivateKey?: string,
    scopedJwtPublicKey?: string
  ) {
    this.algorithm = algorithm;
    
    if (algorithm === 'RS256') {
      if (!jwtPrivateKey || !jwtPublicKey || !scopedJwtPrivateKey || !scopedJwtPublicKey) {
        throw new Error('RSA keys are required when using RS256 algorithm');
      }
      this.jwtKey = jwtPrivateKey;
      this.jwtVerifyKey = jwtPublicKey;
      this.scopedJwtKey = scopedJwtPrivateKey;
      this.scopedJwtVerifyKey = scopedJwtPublicKey;
    } else {
      if (!jwtSecret || !scopedJwtSecret) {
        throw new Error('JWT secrets are required when using HS256 algorithm');
      }
      this.jwtKey = jwtSecret;
      this.jwtVerifyKey = jwtSecret; // For HS256, same secret is used for signing and verification
      this.scopedJwtKey = scopedJwtSecret;
      this.scopedJwtVerifyKey = scopedJwtSecret;
    }
  }

  async verifyToken(token: string): Promise<TokenPayload> {
    try {
      const decoded = jwt.verify(token, this.jwtVerifyKey, {
        algorithms: [this.algorithm]
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
      decoded = jwt.verify(token, this.scopedJwtVerifyKey, {
        algorithms: [this.algorithm]
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

  generateToken(payload: TokenPayload): string {
    return jwt.sign(payload, this.jwtKey, { 
      algorithm: this.algorithm,
      expiresIn: '7d' 
    });
  }

  generateScopedToken(payload: TokenPayload): string {
    return jwt.sign(payload, this.scopedJwtKey, { 
      algorithm: this.algorithm,
      expiresIn: '1h' 
    });
  }
}