import { Cluster, ClusterNode, ClusterOptions, Redis, RedisOptions } from 'ioredis';

import { RedisClusterNode, RedisConfig, RedisMode } from '../types/redis.types';

export type RedisClient = Redis | Cluster;

export interface BuildRedisClientOptions {
  lazyConnect?: boolean;
  retryDelayFactor?: number;
  retryDelayMax?: number;
}

interface ClientLikeConfig {
  host?: string;
  port?: number;
  username?: string;
  password?: string;
  db?: number;
  tls?: boolean;
  connectTimeout?: number;
  maxRetriesPerRequest?: number;
  enableOfflineQueue?: boolean;
  mode?: RedisMode;
  nodes?: RedisClusterNode[];
}

const isClusterClient = (client: RedisClient): client is Cluster =>
  typeof (client as Cluster).nodes === 'function';

const resolveMode = (config: ClientLikeConfig): RedisMode =>
  config.mode === 'cluster' ? 'cluster' : 'standalone';

const baseRedisOptions = (
  config: ClientLikeConfig,
  options: BuildRedisClientOptions,
): RedisOptions => {
  const factor = options.retryDelayFactor ?? 50;
  const max = options.retryDelayMax ?? 2000;
  const opts: RedisOptions = {
    username: config.username,
    password: config.password,
    connectTimeout: config.connectTimeout ?? 10000,
    maxRetriesPerRequest: config.maxRetriesPerRequest ?? 3,
    enableOfflineQueue: config.enableOfflineQueue ?? true,
    lazyConnect: options.lazyConnect ?? false,
    retryStrategy: (times: number) => Math.min(times * factor, max),
  };
  if (config.tls) {
    opts.tls = {};
  }
  return opts;
};

export const buildRedisClient = (
  config: RedisConfig,
  options: BuildRedisClientOptions = {},
): RedisClient => {
  const mode = resolveMode(config);
  const redisOpts = baseRedisOptions(config, options);

  if (mode === 'cluster') {
    if (!config.nodes || config.nodes.length === 0) {
      throw new Error(
        'REDIS_MODE=cluster requires nodes to be set (REDIS_NODES env var).',
      );
    }
    const nodes: ClusterNode[] = config.nodes.map((n) => ({
      host: n.host,
      port: n.port,
    }));
    const clusterOptions: ClusterOptions = {
      redisOptions: redisOpts,
      lazyConnect: options.lazyConnect ?? false,
      scaleReads: 'slave',
      clusterRetryStrategy: (times: number) =>
        Math.min(times * (options.retryDelayFactor ?? 50), options.retryDelayMax ?? 2000),
    };
    return new Cluster(nodes, clusterOptions);
  }

  return new Redis({
    ...redisOpts,
    host: config.host,
    port: config.port,
    db: config.db ?? 0,
  });
};

/**
 * Resolve a list of standalone Redis nodes for SCAN.
 * - Cluster: returns one client per primary (via `nodes('master')`).
 * - Standalone: returns the single client wrapped in an array.
 */
const scanTargets = (client: RedisClient): Redis[] =>
  isClusterClient(client) ? client.nodes('master') : [client as Redis];

/**
 * SCAN that works under both standalone and Cluster.
 * In Cluster mode, SCAN is per-node; we iterate every primary and dedupe.
 */
export const clusterAwareScan = async (
  client: RedisClient,
  pattern: string,
  count = 100,
): Promise<string[]> => {
  const found = new Set<string>();
  for (const node of scanTargets(client)) {
    let cursor = '0';
    do {
      const [nextCursor, keys] = await node.scan(
        cursor,
        'MATCH',
        pattern,
        'COUNT',
        count,
      );
      cursor = nextCursor;
      for (const k of keys) found.add(k);
    } while (cursor !== '0');
  }
  return Array.from(found);
};

/**
 * Cluster-aware equivalent of `KEYS pattern`.
 * Prefer `clusterAwareScan` for production paths — KEYS is O(N) and blocks the server.
 */
export const clusterAwareKeys = async (
  client: RedisClient,
  pattern: string,
): Promise<string[]> => {
  const found = new Set<string>();
  for (const node of scanTargets(client)) {
    const keys = await node.keys(pattern);
    for (const k of keys) found.add(k);
  }
  return Array.from(found);
};
