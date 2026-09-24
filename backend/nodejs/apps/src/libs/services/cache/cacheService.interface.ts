import { CacheOptions } from '../../types/redis.types';

/**
 * Storage-neutral cache contract (R16). Callers depend on this instead of
 * the concrete `RedisService` so a non-Redis cache backend can be swapped
 * in without touching session/health-check call sites.
 */
export interface ICacheService {
  get<T>(key: string, options?: CacheOptions): Promise<T | null>;
  set(key: string, value: unknown, options?: CacheOptions): Promise<void>;
  delete(key: string, options?: CacheOptions): Promise<void>;
  increment(key: string, options?: CacheOptions): Promise<number>;
  disconnect(): Promise<void>;
  isConnected(): boolean;
}
