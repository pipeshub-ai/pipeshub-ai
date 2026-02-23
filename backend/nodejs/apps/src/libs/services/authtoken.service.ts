// authtoken.service.ts
import { injectable } from 'inversify';
import { UnauthorizedError } from '../errors/http.errors';
import { Logger } from '../services/logger.service';
import jwt, { SignOptions } from 'jsonwebtoken';

interface TokenPayload extends Record<string, any> {}
@injectable()
export class AuthTokenService {
  private readonly logger = Logger.getInstance();
  private readonly jwtSecret: string;
  private readonly scopedJwtSecret: string;

  constructor(jwtSecret: string, scopedJwtSecret: string) {
    this.jwtSecret = jwtSecret;
    this.scopedJwtSecret = scopedJwtSecret;
  }

  async verifyToken(token: string): Promise<TokenPayload> {
    try {
      const decoded = jwt.verify(token, this.jwtSecret) as TokenPayload;

      return decoded;
    } catch (error) {
      this.logger.error('Token verification failed', { error });
      throw new UnauthorizedError('Invalid token');
    }
  }

  async verifyScopedToken(token: string, scope: string): Promise<TokenPayload> {
    let decoded: TokenPayload;
    try {
      decoded = jwt.verify(token, this.scopedJwtSecret) as TokenPayload;
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
