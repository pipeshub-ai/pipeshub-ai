import type { Redis } from 'ioredis';

import type { ClientOptions, RedisConnectionConfig } from './connectionConfig';
import type {
  IRedisConnectionProvider,
  RedisClient,
} from './connectionProvider.interface';

/**
 * The provider of a deployment with no Redis server (`REDIS_MODE=postgres` or `inprocess`). Building it costs nothing; asking it for a
 * connection fails with a message that says why, instead of a connection that retries for ever against a host that is not there.
 * What Redis held is elsewhere: sessions and counters in the process, queues and schedules in PostgreSQL.
 */
export class NoRedisProvider implements IRedisConnectionProvider {
  readonly isCluster = false;
  readonly keyNamespace: string;

  constructor(
    config: RedisConnectionConfig,
    readonly mode: string,
  ) {
    this.keyNamespace = config.keyNamespace;
  }

  private refuse(what: string): never {
    throw new Error(
      `REDIS_MODE=${this.mode} runs without a Redis server: ${what} is not available. ` +
        'Use REDIS_MODE=standalone (or cluster) with a Redis, or move this feature to PostgreSQL.',
    );
  }

  getClient(): RedisClient {
    return this.refuse('a Redis client');
  }

  createClient(_options?: ClientOptions): RedisClient {
    return this.refuse('a Redis client');
  }

  createPubSubClient(): Redis {
    return this.refuse('Redis pub/sub');
  }

  prepare(): Promise<void> {
    return Promise.resolve();
  }

  scanKeys(_pattern: string, _count?: number): AsyncIterable<string> {
    return this.refuse('a keyspace scan');
  }

  loadScript(_body: string): Promise<string> {
    return Promise.reject(
      new Error(`REDIS_MODE=${this.mode} runs without a Redis server`),
    );
  }

  keySlot(_key: string): number {
    return 0;
  }

  connectionUrl(): string {
    return this.refuse('a Redis URL');
  }

  ping(): Promise<boolean> {
    return Promise.resolve(false);
  }

  release(_client: RedisClient): void {
    // nothing was handed out
  }

  close(): Promise<void> {
    return Promise.resolve();
  }
}
