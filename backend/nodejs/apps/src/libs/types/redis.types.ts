export interface RedisTlsConfig {
  rejectUnauthorized?: boolean;
  ca?: string;
  cert?: string;
  key?: string;
}

export type RedisMode = 'standalone' | 'cluster';

export interface RedisClusterNode {
  host: string;
  port: number;
}

export interface RedisConfig {
  host: string;
  port: number;
  username?: string;
  password?: string;
  db?: number;
  keyPrefix?: string;
  connectTimeout?: number;
  maxRetriesPerRequest?: number;
  enableOfflineQueue?: boolean;
  tls?: boolean;
  mode?: RedisMode;
  nodes?: RedisClusterNode[];
}

export interface CacheOptions {
  ttl?: number;
  namespace?: string;
}
