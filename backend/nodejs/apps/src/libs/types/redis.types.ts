export interface RedisConfig {
  host: string;
  port: number;
  password?: string;
  db?: number;
  keyPrefix?: string;
  connectTimeout?: number;
  maxRetriesPerRequest?: number;
  enableOfflineQueue?: boolean;
}

export interface CacheOptions {
  ttl?: number;
  namespace?: string;
}
