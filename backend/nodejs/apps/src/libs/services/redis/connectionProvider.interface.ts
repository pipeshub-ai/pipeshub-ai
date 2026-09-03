import type { Cluster, Redis } from 'ioredis';

import type { ClientOptions } from './connectionConfig';

export type RedisClient = Redis | Cluster;

/**
 * Owns Redis topology; every feature/algorithm depends on this, not a
 * concrete `ioredis` client class.
 *
 * No feature code should `import Redis from 'ioredis'` (or `Cluster`)
 * directly -- see `tests/libs/services/redis/architectureGuard.test.ts`.
 * Everything goes through a provider obtained from
 * `RedisConnectionProviderFactory` / `getRedisProvider()`.
 */
export interface IRedisConnectionProvider {
  /**
   * Shared client for request/response traffic. Standalone binds one
   * client for the process; cluster implementations may do the same
   * internally.
   */
  getClient(): RedisClient;

  /** Fresh, caller-owned client for blocking reads / pub-sub / worker connections. */
  createClient(options?: ClientOptions): RedisClient;

  /**
   * Plain connection suitable for `SUBSCRIBE` (R13). Cluster implementations
   * hand back a connection to a single node; regular (non-sharded)
   * `PUBLISH` still propagates cluster-wide, so any subscriber sees it
   * regardless of which node it is subscribed to.
   */
  createPubSubClient(): Redis;

  /**
   * Keyspace-wide SCAN (R2). Cluster implementations fan out over every
   * master. Streamed rather than returned as an array: `listTopics()` and
   * the KV-store migration scan broad patterns, and materialising a whole
   * production keyspace to count it is how a `KEYS`-shaped memory spike
   * gets reintroduced.
   */
  scanKeys(pattern: string, count?: number): AsyncIterable<string>;

  /**
   * `SCRIPT LOAD` everywhere the script may execute; returns its SHA (R6).
   * Cluster implementations load on every master so a subsequent `EVALSHA`
   * against any key never hits `NOSCRIPT`.
   */
  loadScript(body: string): Promise<string>;

  /** Hash slot for `key`. Standalone returns 0 for every key (R1). */
  keySlot(key: string): number;

  /**
   * A `redis://` URL for consumers that build their own client (BullMQ can
   * take a `connection` object instead, but some libraries only accept a
   * URL). Throws on cluster providers -- callers must use a
   * cluster-capable client library or accept the standalone-only
   * restriction (R7 equivalent).
   */
  connectionUrl(): string;

  ping(): Promise<boolean>;

  /**
   * Stop tracking a client the caller closed itself, so long-lived
   * reconnect loops do not accumulate dead clients for the process's life.
   */
  release(client: RedisClient): void;

  /** Close every client this provider handed out. */
  close(): Promise<void>;

  readonly isCluster: boolean;

  /** The registered mode name this instance was created under (e.g. `standalone`). */
  readonly mode: string;

  /**
   * `REDIS_KEY_NAMESPACE`, or `''` when unset (R9). Callers building an
   * explicit key (`buildKey` helpers, fixed prefix constants) or a pub/sub
   * channel name prepend this themselves -- it is never applied as an
   * ioredis `keyPrefix`, which silently misses `SCAN` patterns, Lua script
   * bodies, and channel names.
   */
  readonly keyNamespace: string;
}
