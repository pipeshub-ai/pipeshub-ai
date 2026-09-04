/**
 * OSS default provider: a single standalone Redis (or a Sentinel/replica
 * pair fronted by one endpoint) via `ioredis.Redis`.
 */
import { Redis, RedisOptions } from 'ioredis';

import {
  ClientOptions,
  DEFAULT_CLIENT_OPTIONS,
  RedisConnectionConfig,
} from './connectionConfig';
import { IRedisConnectionProvider } from './connectionProvider.interface';
import { Logger } from '../logger.service';
import { readTlsCaCertificate } from './tlsCa';

const logger = Logger.getInstance({ service: 'redis-standalone-provider' });

function retryStrategy(times: number): number {
  return Math.min(times * 50, 2000);
}

export class StandaloneRedisProvider implements IRedisConnectionProvider {
  private readonly config: RedisConnectionConfig;
  private sharedClient: Redis | null = null;
  private readonly createdClients = new Set<Redis>();
  private readonly caCertificate: Buffer | undefined;
  private loggedStartup = false;

  constructor(config: RedisConnectionConfig) {
    this.config = config;
    this.caCertificate = readTlsCaCertificate(config.tlsCaPath);
    this.logStartupOnce();
  }

  private logStartupOnce(): void {
    if (this.loggedStartup) {
      return;
    }
    this.loggedStartup = true;
    logger.info('Redis connection provider', {
      mode: 'standalone',
      host: this.config.host,
      port: this.config.port,
      db: this.config.db,
      namespace: this.config.keyNamespace || '(none)',
      tls: this.config.tls,
    });
    if (this.config.db) {
      logger.warn(
        `REDIS_DB=${this.config.db} is deprecated; prefer REDIS_KEY_NAMESPACE for tenant isolation. Ignored entirely in cluster mode.`,
      );
    }
    if (this.config.tls && !this.config.tlsRejectUnauthorized) {
      // Kept as an escape hatch (self-signed certs, cert-rotation windows)
      // but never silent: with verification off the connection is encrypted
      // yet unauthenticated, so it does not protect against an active
      // man-in-the-middle. REDIS_TLS_CA_PATH is the fix for a private CA.
      logger.warn(
        'REDIS_TLS_REJECT_UNAUTHORIZED=false: Redis TLS certificates are NOT ' +
          'verified, so the connection is encrypted but not authenticated. Set ' +
          'REDIS_TLS_CA_PATH to trust a private CA instead of disabling verification.',
      );
    }
  }

  private connectionOptions(options: ClientOptions): RedisOptions {
    // `??`, not a plain spread: a caller-supplied key set to `undefined`
    // (e.g. forwarding an optional config field as-is) must still fall
    // back to the default, not pin the option to `undefined`.
    const merged: Required<ClientOptions> = {
      blocking: options.blocking ?? DEFAULT_CLIENT_OPTIONS.blocking,
      connectTimeoutMs:
        options.connectTimeoutMs ?? this.config.connectTimeoutMs,
      maxRetriesPerRequest:
        options.maxRetriesPerRequest ?? DEFAULT_CLIENT_OPTIONS.maxRetriesPerRequest,
      enableOfflineQueue:
        options.enableOfflineQueue ?? DEFAULT_CLIENT_OPTIONS.enableOfflineQueue,
    };
    const opts: RedisOptions = {
      host: this.config.host,
      port: this.config.port,
      db: this.config.db,
      connectTimeout: merged.connectTimeoutMs,
      maxRetriesPerRequest: merged.blocking
        ? null
        : merged.maxRetriesPerRequest,
      enableOfflineQueue: merged.enableOfflineQueue,
      lazyConnect: merged.blocking,
      retryStrategy,
    };
    if (this.config.username) {
      opts.username = this.config.username;
    }
    if (this.config.password) {
      opts.password = this.config.password;
    }
    if (this.config.tls) {
      opts.tls = {
        rejectUnauthorized: this.config.tlsRejectUnauthorized,
        ca: this.caCertificate,
      };
    }
    return opts;
  }

  private track(client: Redis): Redis {
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
  release(client: Redis): void {
    this.createdClients.delete(client);
  }

  getClient(): Redis {
    if (this.sharedClient) {
      return this.sharedClient;
    }
    this.sharedClient = this.track(new Redis(this.connectionOptions({})));
    return this.sharedClient;
  }

  createClient(options: ClientOptions = {}): Redis {
    return this.track(new Redis(this.connectionOptions(options)));
  }

  createPubSubClient(): Redis {
    return this.createClient({ blocking: true });
  }

  async *scanKeys(pattern: string, count = 100): AsyncIterable<string> {
    const client = this.getClient();
    let cursor = '0';
    do {
      const [nextCursor, found] = await client.scan(
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

  async loadScript(body: string): Promise<string> {
    const client = this.getClient();
    const sha = await client.script('LOAD', body);
    return String(sha);
  }

  keySlot(_key: string): number {
    return 0;
  }

  connectionUrl(): string {
    const scheme = this.config.tls ? 'rediss' : 'redis';
    let auth = '';
    if (this.config.username) {
      auth = this.config.username;
      if (this.config.password) {
        auth += `:${this.config.password}`;
      }
      auth += '@';
    } else if (this.config.password) {
      auth = `:${this.config.password}@`;
    }
    return `${scheme}://${auth}${this.config.host}:${this.config.port}/${this.config.db}`;
  }

  async ping(): Promise<boolean> {
    try {
      const result = await this.getClient().ping();
      return result === 'PONG';
    } catch (error) {
      logger.debug('Redis ping failed', { error });
      return false;
    }
  }

  async close(): Promise<void> {
    const clients = Array.from(this.createdClients);
    this.createdClients.clear();
    this.sharedClient = null;
    await Promise.all(
      clients.map(async (client) => {
        try {
          await client.quit();
        } catch (error) {
          logger.debug('Error closing Redis client', { error });
        }
      }),
    );
  }

  get isCluster(): boolean {
    return false;
  }

  get mode(): string {
    return 'standalone';
  }

  get keyNamespace(): string {
    return this.config.keyNamespace;
  }
}
