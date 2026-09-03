/**
 * Generic OSS Redis Cluster provider (Open Decision 2): Redis Cluster is
 * plain open-source Redis, so OSS ships this both so `REDIS_MODE=cluster`
 * works against a self-hosted cluster and so OSS CI can run the full
 * contract-test suite against a real 3-master cluster. AWS MemoryDB is
 * protocol-compatible with Redis Cluster; an EE `memorydb` provider extends
 * this to add IAM credential rotation, a NAT map, and TLS-by-default, then
 * registers itself with `RedisConnectionProviderFactory`.
 */
import { Cluster, ClusterOptions, Redis } from 'ioredis';

import {
  ClientOptions,
  DEFAULT_CLIENT_OPTIONS,
  RedisConnectionConfig,
} from './connectionConfig';
import { IRedisConnectionProvider } from './connectionProvider.interface';
import { Logger } from '../logger.service';
import { readTlsCaCertificate } from './tlsCa';

const logger = Logger.getInstance({ service: 'redis-cluster-provider' });

function retryStrategy(times: number): number {
  return Math.min(times * 50, 2000);
}

export class ClusterRedisProvider implements IRedisConnectionProvider {
  protected readonly config: RedisConnectionConfig;
  private sharedClient: Cluster | null = null;
  private readonly createdClients = new Set<Cluster>();
  private readonly pubSubClients: Redis[] = [];
  private readonly caCertificate: Buffer | undefined;

  constructor(config: RedisConnectionConfig) {
    this.config = config;
    this.caCertificate = readTlsCaCertificate(config.tlsCaPath);
    logger.info('Redis connection provider', {
      mode: 'cluster',
      endpoints: this.startupNodesRepr(),
      namespace: this.config.keyNamespace || '(none)',
      tls: this.config.tls,
      scaleReads: this.config.scaleReads,
    });
  }

  private startupNodesRepr(): string {
    if (this.config.clusterEndpoints.length > 0) {
      return this.config.clusterEndpoints.join(',');
    }
    return `${this.config.host}:${this.config.port}`;
  }

  private startupNodes(): Array<{ host: string; port: number }> {
    if (this.config.clusterEndpoints.length > 0) {
      return this.config.clusterEndpoints.map((endpoint) => {
        const [host = '', port] = endpoint.split(':');
        return { host, port: port ? parseInt(port, 10) : 6379 };
      });
    }
    return [{ host: this.config.host, port: this.config.port }];
  }

  protected clusterOptions(options: ClientOptions): ClusterOptions {
    // `??`, not a plain spread: a caller-supplied key set to `undefined`
    // must still fall back to the default, not pin the option to `undefined`.
    const merged: Required<ClientOptions> = {
      blocking: options.blocking ?? DEFAULT_CLIENT_OPTIONS.blocking,
      connectTimeoutMs:
        options.connectTimeoutMs ?? this.config.connectTimeoutMs,
      maxRetriesPerRequest:
        options.maxRetriesPerRequest ?? DEFAULT_CLIENT_OPTIONS.maxRetriesPerRequest,
      enableOfflineQueue:
        options.enableOfflineQueue ?? DEFAULT_CLIENT_OPTIONS.enableOfflineQueue,
    };
    // Typed without the `| undefined` from ClusterOptions['redisOptions']:
    // it is always initialised to a literal below, but that field's type
    // includes `undefined` (no per-node options set), which would otherwise
    // force non-null checks on every property assignment that follows.
    const redisOptions: NonNullable<ClusterOptions['redisOptions']> = {
      connectTimeout: merged.connectTimeoutMs,
      maxRetriesPerRequest: merged.blocking
        ? null
        : merged.maxRetriesPerRequest,
    };
    if (this.config.username) {
      redisOptions.username = this.config.username;
    }
    if (this.config.password) {
      redisOptions.password = this.config.password;
    }
    if (this.config.tls) {
      redisOptions.tls = {
        rejectUnauthorized: this.config.tlsRejectUnauthorized,
        ca: this.caCertificate,
      };
    }
    return {
      redisOptions,
      scaleReads: this.config.scaleReads,
      retryDelayOnFailover: 100,
      clusterRetryStrategy: retryStrategy,
      enableOfflineQueue: merged.enableOfflineQueue,
    };
  }

  private track(client: Cluster): Cluster {
    this.createdClients.add(client);
    return client;
  }

  /**
   * Stop tracking a client the caller has closed itself.
   *
   * Without this the streams reconnect path -- which builds three fresh
   * clients every time it cycles -- grows this set for the life of the
   * process, holding a reference to every dead client it ever replaced.
   */
  release(client: Cluster): void {
    this.createdClients.delete(client);
  }

  getClient(): Cluster {
    if (this.sharedClient) {
      return this.sharedClient;
    }
    this.sharedClient = this.track(
      new Cluster(this.startupNodes(), this.clusterOptions({})),
    );
    return this.sharedClient;
  }

  createClient(options: ClientOptions = {}): Cluster {
    return this.track(new Cluster(this.startupNodes(), this.clusterOptions(options)));
  }

  /**
   * A *dedicated* plain connection to one discovered node (R13); regular
   * (non-sharded) PUBLISH propagates cluster-wide, so any subscriber sees it.
   *
   * Deliberately not one of `client.nodes('master')`: those connections are
   * the shared cluster client's own, and `SUBSCRIBE` puts a connection into
   * subscriber mode where it can no longer serve ordinary commands -- a
   * caller that also closes it would take the shared client down with it.
   */
  createPubSubClient(): Redis {
    const client = this.getClient();
    const node = client.nodes('master')[0];
    if (!node) {
      throw new Error(
        'Redis Cluster has no reachable master node for pub/sub; connect the ' +
          'cluster client before requesting a pub/sub connection.',
      );
    }
    const { host, port } = node.options;
    const dedicated = new Redis({
      ...this.clusterOptions({ blocking: true }).redisOptions,
      host,
      port,
      lazyConnect: true,
      retryStrategy,
    });
    this.pubSubClients.push(dedicated);
    return dedicated;
  }

  /**
   * Every master, once the slot map is loaded.
   *
   * `nodes('master')` reads ioredis' cached topology, which is empty until
   * the cluster has connected -- so calling it on a freshly constructed
   * client returns `[]`, and a fan-out over `[]` silently reports an empty
   * keyspace instead of failing. `ping()` forces the connection and the
   * slot-map refresh first.
   */
  private async masters(): Promise<Redis[]> {
    const client = this.getClient();
    if (client.status !== 'ready') {
      await client.ping();
    }
    const nodes = client.nodes('master');
    if (nodes.length === 0) {
      throw new Error(
        'Redis Cluster reported no reachable primaries; refusing to report a ' +
          'partial keyspace. Check REDIS_CLUSTER_ENDPOINTS and cluster health.',
      );
    }
    return nodes;
  }

  /**
   * Keyspace-wide SCAN (R2): ioredis' `Cluster.scan()` only hits one node,
   * so this fans out over every master explicitly, matching the Python
   * `ClusterRedisProvider.scan_keys` behaviour.
   */
  async *scanKeys(pattern: string, count = 100): AsyncIterable<string> {
    for (const node of await this.masters()) {
      let cursor = '0';
      do {
        const [nextCursor, found] = await node.scan(
          cursor,
          'MATCH',
          pattern,
          'COUNT',
          count,
        );
        cursor = nextCursor;
        yield* found;
      } while (cursor !== '0');
    }
  }

  /** `SCRIPT LOAD` on every master so a later `EVALSHA` never hits `NOSCRIPT` (R6). */
  async loadScript(body: string): Promise<string> {
    let sha: string | null = null;
    for (const node of await this.masters()) {
      sha = String(await node.script('LOAD', body));
    }
    if (sha === null) {
      throw new Error('Redis Cluster has no reachable primaries to load script on');
    }
    return sha;
  }

  keySlot(key: string): number {
    return calcSlotFallback(key);
  }

  connectionUrl(): string {
    throw new Error(
      'Redis Cluster has no single connection URL; Celery/BullMQ callers must ' +
        'use a cluster-capable client or a non-cluster broker instead (R7).',
    );
  }

  async ping(): Promise<boolean> {
    try {
      const result = await this.getClient().ping();
      return result === 'PONG';
    } catch (error) {
      logger.debug('Redis Cluster ping failed', { error });
      return false;
    }
  }

  async close(): Promise<void> {
    const clients: Array<Cluster | Redis> = [
      ...this.pubSubClients.splice(0, this.pubSubClients.length),
      ...this.createdClients,
    ];
    this.createdClients.clear();
    this.sharedClient = null;
    await Promise.all(
      clients.map(async (client) => {
        try {
          await client.quit();
        } catch (error) {
          logger.debug('Error closing Redis Cluster client', { error });
        }
      }),
    );
  }

  get isCluster(): boolean {
    return true;
  }

  get mode(): string {
    return 'cluster';
  }

  get keyNamespace(): string {
    return this.config.keyNamespace;
  }
}

/**
 * ioredis does not export its internal CRC-16 slot calculator as a stable
 * public API across versions, so we ship the standard Redis Cluster
 * CRC16(key) mod 16384 implementation used to pre-group multi-stream
 * XREADGROUP calls by slot (R1). This must stay behaviourally identical to
 * the Python implementation's `redis.crc.key_slot`.
 */
const CRC16_TABLE = ((): Uint16Array => {
  const table = new Uint16Array(256);
  for (let i = 0; i < 256; i += 1) {
    let crc = i << 8;
    for (let j = 0; j < 8; j += 1) {
      crc = (crc & 0x8000) !== 0 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
    }
    table[i] = crc;
  }
  return table;
})();

function crc16(buf: Buffer): number {
  let crc = 0;
  for (let i = 0; i < buf.length; i += 1) {
    // Loop bound guarantees buf[i] and the 0-255 table index are defined.
    crc = ((crc << 8) & 0xffff) ^ CRC16_TABLE[((crc >> 8) ^ buf[i]!) & 0xff]!;
  }
  return crc & 0xffff;
}

function calcSlotFallback(key: string): number {
  let hashKey = key;
  const start = key.indexOf('{');
  if (start !== -1) {
    const end = key.indexOf('}', start + 1);
    if (end !== -1 && end !== start + 1) {
      hashKey = key.slice(start + 1, end);
    }
  }
  return crc16(Buffer.from(hashKey)) % 16384;
}
