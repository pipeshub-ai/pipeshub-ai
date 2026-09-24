/**
 * Stand-in for an EE provider module (e.g. `@pipeshub-ee/redis-memorydb`):
 * registers a mode as a side effect of being imported, and nothing else.
 * Loaded via `REDIS_PROVIDER_MODULE` + `ensureProviderModuleLoaded()` (R10)
 * in the `connectionProviderFactory.test.ts` success-path case (T1) --
 * Node's counterpart to Python's `test_module_registering_a_mode_makes_it_available`.
 */
import { Redis } from 'ioredis';
import { RedisConnectionConfig } from '../../src/libs/services/redis/connectionConfig';
import {
  IRedisConnectionProvider,
  RedisClient,
} from '../../src/libs/services/redis/connectionProvider.interface';
import { RedisConnectionProviderFactory } from '../../src/libs/services/redis/connectionProviderFactory';

export const FIXTURE_MODE = 'fake-fixture';

export class FixtureRedisProvider implements IRedisConnectionProvider {
  readonly isCluster = false;
  readonly mode = FIXTURE_MODE;

  constructor(public readonly config: RedisConnectionConfig) {}

  get keyNamespace(): string {
    return this.config.keyNamespace;
  }

  getClient(): RedisClient {
    return {} as RedisClient;
  }

  createClient(): RedisClient {
    return {} as RedisClient;
  }

  createPubSubClient(): Redis {
    return {} as Redis;
  }

  async prepare(): Promise<void> {}

  release(): void {
    /* no-op */
  }

  async *scanKeys(): AsyncIterable<string> {
    /* empty */
  }

  async loadScript(): Promise<string> {
    return 'sha';
  }

  keySlot(): number {
    return 0;
  }

  connectionUrl(): string {
    return 'redis://fake-fixture';
  }

  async ping(): Promise<boolean> {
    return true;
  }

  async close(): Promise<void> {}
}

RedisConnectionProviderFactory.register(
  FIXTURE_MODE,
  (config) => new FixtureRedisProvider(config),
);
