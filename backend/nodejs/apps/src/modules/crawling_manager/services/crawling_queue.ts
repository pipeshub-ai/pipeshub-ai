import { RedisConfig } from '../../../libs/types/redis.types';
import { redisConnectionConfigFromHostPort } from '../../../libs/services/redis/connectionConfig';
import { getRedisProvider } from '../../../libs/services/redis/connectionProviderFactory';
import { IRedisConnectionProvider } from '../../../libs/services/redis/connectionProvider.interface';

/**
 * Shared BullMQ wiring for the crawling queue.
 *
 * The scheduler and the worker must agree on the prefix down to the character
 * — BullMQ addresses a queue by `<prefix>:<queue name>`, so a mismatch leaves
 * the worker listening on a queue nothing writes to, with no error anywhere.
 * Keeping the derivation in one place is the only way that stays true when
 * `REDIS_KEY_NAMESPACE` is set.
 */

/**
 * The hash tag is what makes BullMQ usable on Redis Cluster / MemoryDB at all:
 * its job-state transitions are Lua scripts touching several keys per queue,
 * so every key has to hash to one slot. A namespace goes *inside* the braces
 * so two deployments sharing one endpoint get different slots rather than
 * colliding on the same one.
 */
export function crawlingQueuePrefix(provider: IRedisConnectionProvider): string {
  const namespace = provider.keyNamespace;
  return namespace ? `{${namespace}-crawling}` : '{crawling}';
}

export function crawlingRedisProvider(
  redisConfig: RedisConfig,
): IRedisConnectionProvider {
  return getRedisProvider(
    redisConnectionConfigFromHostPort({
      host: redisConfig.host,
      port: redisConfig.port,
      username: redisConfig.username,
      password: redisConfig.password,
      db: redisConfig.db,
      tls: redisConfig.tls,
    }),
  );
}
