import { v4 as uuidv4 } from 'uuid';
import { injectable, inject } from 'inversify';
import { RedisService } from '../../../libs/services/redis.service';
import { RedisServiceNotInitializedError } from '../../../libs/errors/redis.errors';

export interface SessionData {
  token?: string;
  userId: string;
  email: string;
  isAuthenticated?: boolean;
  [key: string]: any; // Allows additional properties
}
const SESSION_EXPIRY = 3600; // 1 hour in seconds

@injectable()
export class SessionService {
  constructor(
    @inject('RedisService') private redisService: RedisService,
  ) {}

  async createSession(
    sessionData: { userId: string; email: string } & Partial<SessionData>,
  ): Promise<SessionData> {
    if (!this.redisService)
      throw new RedisServiceNotInitializedError('Redis service is not initialized.');

    const token = uuidv4();
    const session: SessionData = { ...sessionData, token };
    await this.redisService.set(`session:${token}`, session, {
      ttl: SESSION_EXPIRY,
    });

    return session;
  }

  async getSession(token: string): Promise<SessionData | null> {
    if (!this.redisService)
      throw new RedisServiceNotInitializedError('Redis service is not initialized.');

    return await this.redisService.get<SessionData>(`session:${token}`);
  }

  async updateSession(session: SessionData): Promise<void> {
    if (!this.redisService)
      throw new RedisServiceNotInitializedError('Redis service is not initialized.');

    await this.redisService.set(`session:${session.token}`, session, {
      ttl: SESSION_EXPIRY,
    });
  }

  async completeAuthentication(session: SessionData): Promise<void> {
    if (!session.token) {
      throw new Error('Session token is missing');
    }

    session.isAuthenticated = true;
    await this.deleteSession(session.token);
  }

  async deleteSession(token: string): Promise<void> {
    if (!this.redisService)
      throw new Error('Redis service is not initialized.');

    await this.redisService.delete(`session:${token}`);
  }

  async extendSession(token: string): Promise<void> {
    if (!this.redisService)
      throw new Error('Redis service is not initialized.');

    await this.redisService.increment(`session:${token}`, {
      ttl: SESSION_EXPIRY,
    });
  }
}
