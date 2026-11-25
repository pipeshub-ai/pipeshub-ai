import type { RedisConfig } from '../../../../libs/types/redis.types';
import type { IRedisConnectionProvider } from '../../../../libs/services/redis/connectionProvider.interface';
import { getRedisProvider } from '../../../../libs/services/redis/connectionProviderFactory';
import { redisConnectionConfigFromHostPort } from '../../../../libs/services/redis/connectionConfig';

/**
 * The Redis the workers publish live frames, wake-ups and run-ready notices
 * on, built from every field of the configured Redis. The worker reads the
 * same configuration whole (`ConfigurationService.get_redis_config`), so a
 * field left out here is a different Redis: without `db`, Node followed DB 0
 * while the worker wrote its streams to `REDIS_DB`, the notify channel (pub/sub
 * spans databases) woke a reader that found nothing, and every answer reached
 * the browser at once from the journal at the turn's end (WP88-FU-1).
 */
export function harnessRedisProvider(
  config: RedisConfig,
): IRedisConnectionProvider {
  return getRedisProvider(
    redisConnectionConfigFromHostPort({
      host: config.host,
      port: config.port,
      username: config.username === '' ? undefined : config.username,
      password: config.password,
      db: config.db,
      tls: config.tls,
    }),
  );
}
