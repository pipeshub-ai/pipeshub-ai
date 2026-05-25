import { Cluster, ClusterNode, ClusterOptions, Redis, RedisOptions } from 'ioredis';

import { RedisClusterNode, RedisConfig, RedisMode } from '../types/redis.types';

export type RedisClient = Redis | Cluster;

/**
 * Parse a comma-separated `host:port` list (REDIS_NODES) into cluster nodes.
 *
 * Splits each entry on the LAST colon so IPv6 literals (`[::1]:6379`,
 * `fe80::1:6379`) parse correctly. A missing/empty port defaults to 6379; a
 * non-numeric port throws a descriptive startup error. Shared by config.ts
 * and cm.service.ts so the two stay consistent.
 */
export const parseRedisNodes = (raw?: string): RedisClusterNode[] => {
  const nodes: RedisClusterNode[] = [];
  for (const rawEntry of (raw ?? '').split(',')) {
    const entry = rawEntry.trim();
    if (!entry) continue;
    const lastColon = entry.lastIndexOf(':');
    const host = lastColon === -1 ? entry : entry.slice(0, lastColon);
    const portStr = lastColon === -1 ? '6379' : entry.slice(lastColon + 1);
    if (!host) continue;
    const port = parseInt(portStr || '6379', 10);
    if (Number.isNaN(port)) {
      throw new Error(`REDIS_NODES entry has non-numeric port: '${entry}'`);
    }
    nodes.push({ host, port });
  }
  return nodes;
};

export interface BuildRedisClientOptions {
  lazyConnect?: boolean;
  retryDelayFactor?: number;
  retryDelayMax?: number;
  /**
   * Override `maxRetriesPerRequest`. Pass `null` for blocking clients (e.g.
   * BullMQ workers) where the request layer must not auto-retry. `undefined`
   * (the default) uses the factory's regular default of 3.
   */
  maxRetriesPerRequest?: number | null;
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

/**
 * Resolve the retry-delay parameters once so the standalone `retryStrategy`
 * and the cluster `clusterRetryStrategy` can never drift apart.
 */
const resolveRetryParams = (options: BuildRedisClientOptions) => ({
  factor: options.retryDelayFactor ?? 50,
  max: options.retryDelayMax ?? 2000,
});

const retryDelay = (
  params: { factor: number; max: number },
): ((times: number) => number) =>
  (times: number) => Math.min(times * params.factor, params.max);

const baseRedisOptions = (
  config: ClientLikeConfig,
  options: BuildRedisClientOptions,
  retry: { factor: number; max: number },
): RedisOptions => {
  // Explicit `null` override (e.g. BullMQ workers) must survive the
  // nullish-coalescing fallback, hence the `'maxRetriesPerRequest' in options`
  // check rather than `??`.
  const maxRetriesPerRequest =
    'maxRetriesPerRequest' in options
      ? options.maxRetriesPerRequest
      : config.maxRetriesPerRequest ?? 3;
  const opts: RedisOptions = {
    username: config.username,
    password: config.password,
    connectTimeout: config.connectTimeout ?? 10000,
    maxRetriesPerRequest: maxRetriesPerRequest as number | null | undefined,
    enableOfflineQueue: config.enableOfflineQueue ?? true,
    lazyConnect: options.lazyConnect ?? false,
    retryStrategy: retryDelay(retry),
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
  const retry = resolveRetryParams(options);
  const redisOpts = baseRedisOptions(config, options, retry);

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
      clusterRetryStrategy: retryDelay(retry),
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

/**
 * Wrap a BullMQ queue name with a Redis Cluster hash tag in cluster mode.
 *
 * BullMQ's internal Lua scripts touch many keys per queue (`<queue>:wait`,
 * `<queue>:active`, `<queue>:delayed`, `<queue>:meta`, etc.). On Redis
 * Cluster these all need to live on the same shard or BullMQ returns
 * CROSSSLOT. Wrapping the queue name with `{...}` co-locates them.
 *
 * Standalone mode passes through unchanged so existing deployments aren't
 * affected.
 */
export const bullQueueName = (
  config: { mode?: RedisMode },
  name: string,
): string => (config.mode === 'cluster' ? `{${name}}` : name);

/**
 * Build the `connection` value to pass to BullMQ's Queue/Worker constructor.
 *
 * - Standalone mode: returns a plain options object so BullMQ creates its
 *   own ioredis client internally (matches the existing behaviour).
 * - Cluster mode: returns a live `Cluster` instance from our factory. BullMQ
 *   accepts an existing client and skips creating its own. Without this,
 *   BullMQ would build a standalone Redis pointing at one cluster node and
 *   trip CROSSSLOT on every multi-key Lua call.
 */
export const buildBullConnection = (
  config: RedisConfig,
): RedisClient | {
  host: string;
  port: number;
  username?: string;
  password?: string;
  db?: number;
  maxRetriesPerRequest: null;
} => {
  if (config.mode === 'cluster') {
    // BullMQ blocks on commands like BRPOPLPUSH; ioredis's per-request retry
    // must be disabled (`maxRetriesPerRequest: null`) or BullMQ throws on
    // construction. Same applies for the standalone branch below.
    return buildRedisClient(config, { maxRetriesPerRequest: null });
  }
  return {
    host: config.host,
    port: config.port,
    username: config.username,
    password: config.password,
    db: config.db ?? 0,
    maxRetriesPerRequest: null,
  };
};
