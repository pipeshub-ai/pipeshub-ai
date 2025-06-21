import { BaseError } from './base.error';

export class RedisError extends BaseError {
  constructor(message: string, metadata?: any) {
    super('REDIS_ERROR', message, 500, metadata);
  }
}

export class RedisServiceNotInitializedError extends RedisError {
  constructor(message: string, metadata?: any) {
    super(`Redis service not initialized: ${message}`, metadata);
  }
}

export class RedisConnectionError extends RedisError {
  constructor(message: string, metadata?: any) {
    super(`Redis connection error: ${message}`, metadata);
  }
}

export class RedisCacheError extends RedisError {
  constructor(message: string, metadata?: any) {
    super(`Redis cache error: ${message}`, metadata);
  }
}
