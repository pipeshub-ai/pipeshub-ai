/**
 * Factory + process-level singleton registry for `IRedisConnectionProvider`.
 *
 * An EE repo adds MemoryDB support entirely through this module's
 * extension points, with zero changes to any file in this package:
 *
 * 1. Implement `IRedisConnectionProvider` (or extend `ClusterRedisProvider`).
 * 2. Call `RedisConnectionProviderFactory.register('memorydb', (config) => new MemoryDBProvider(config))`
 *    at import time.
 * 3. Set `REDIS_PROVIDER_MODULE=@pipeshub-ee/redis-memorydb-provider` (or any
 *    module resolvable from this process) and `REDIS_MODE=memorydb`.
 */
import {
  RedisConnectionConfig,
  redisConnectionConfigFromEnv,
} from './connectionConfig';
import { ClusterRedisProvider } from './clusterRedisProvider';
import { IRedisConnectionProvider } from './connectionProvider.interface';
import { StandaloneRedisProvider } from './standaloneRedisProvider';
import { Logger } from '../logger.service';

const logger = Logger.getInstance({ service: 'redis-provider-factory' });

type ProviderCtor = (config: RedisConnectionConfig) => IRedisConnectionProvider;

export class RedisConnectionProviderFactory {
  private static registry = new Map<string, ProviderCtor>();
  private static discoveredModules = new Set<string>();

  static register(mode: string, providerFactory: ProviderCtor): void {
    this.registry.set(mode, providerFactory);
  }

  static registeredModes(): string[] {
    return Array.from(this.registry.keys()).sort();
  }

  /** Synchronous creation for the common case: OSS modes are registered eagerly below. */
  static create(
    config?: RedisConnectionConfig,
    mode?: string,
  ): IRedisConnectionProvider {
    const resolvedConfig = config ?? redisConnectionConfigFromEnv();
    const resolvedMode = mode ?? process.env.REDIS_MODE ?? 'standalone';

    const providerFactory = this.registry.get(resolvedMode);
    if (!providerFactory) {
      throw new Error(
        `Unknown REDIS_MODE '${resolvedMode}'; registered modes: ` +
          `${this.registeredModes().join(', ')}. If this mode ships in an EE ` +
          'module, call ensureProviderModuleLoaded() during app bootstrap ' +
          'before creating the provider.',
      );
    }

    // Credentials over an unauthenticated channel (CWE-295). TLS with
    // verification off is encrypted but *unauthenticated*: any MITM can
    // present a self-signed cert, terminate the session, and harvest the
    // password. Enforced here rather than in each provider so every
    // implementation -- including one registered by an EE repo -- is covered.
    if (
      resolvedConfig.tls &&
      !resolvedConfig.tlsRejectUnauthorized &&
      (resolvedConfig.password || resolvedConfig.username)
    ) {
      throw new Error(
        'REDIS_TLS_REJECT_UNAUTHORIZED=false with Redis credentials set: the ' +
          'connection would be encrypted but not authenticated, so the password ' +
          'is exposed to anyone who can intercept it. Point REDIS_TLS_CA_PATH at ' +
          'the CA that signed your Redis certificate instead of disabling ' +
          'verification.',
      );
    }

    if (resolvedMode !== 'standalone' && resolvedConfig.db) {
      throw new Error(
        'REDIS_DB is not supported outside standalone mode ' +
          `(REDIS_MODE=${resolvedMode}); use REDIS_KEY_NAMESPACE for tenant ` +
          'isolation instead.',
      );
    }

    return providerFactory(resolvedConfig);
  }

  /**
   * Import `REDIS_PROVIDER_MODULE` so an EE provider can self-register (R10).
   * Node has no dependency-free entry-point mechanism equivalent to Python's
   * `importlib.metadata.entry_points`, so this single env-driven hook is the
   * only discovery path; call it once during app bootstrap (`preInit`)
   * before any Redis-backed service is constructed.
   */
  static async ensureProviderModuleLoaded(): Promise<void> {
    const moduleName = process.env.REDIS_PROVIDER_MODULE;
    if (!moduleName || this.discoveredModules.has(moduleName)) {
      return;
    }
    this.discoveredModules.add(moduleName);
    try {
      await import(moduleName);
      logger.info(`Loaded Redis provider module '${moduleName}'`);
    } catch (error) {
      logger.error(`Failed to import REDIS_PROVIDER_MODULE '${moduleName}'`, {
        error,
      });
    }
  }

  /** Test-only: drop registrations added by a test so later tests are isolated. */
  static resetForTests(modes: string[]): void {
    for (const mode of modes) {
      this.registry.delete(mode);
    }
    this.discoveredModules.clear();
  }
}

// --- Process-level singleton accessor (R11) ---------------------------------
//
// Every Redis-backed service in Node today constructs its own client
// independently (RedisService, RedisDistributedKeyValueStore, redis-streams
// producer/consumer/admin). Cache by config fingerprint so the common case
// -- every caller sharing the same REDIS_* env -- collapses onto one
// provider (and, on cluster, one connection to every node) per process.

const providerCache = new Map<string, IRedisConnectionProvider>();

function fingerprint(config: RedisConnectionConfig, mode: string): string {
  // Every field consumed by StandaloneRedisProvider.connectionOptions() /
  // ClusterRedisProvider.clusterOptions() must be here: two configs that
  // differ in any of these need distinct provider instances, or the second
  // caller silently reuses the first caller's connections (wrong creds/TLS).
  return JSON.stringify([
    mode,
    config.host,
    config.port,
    config.username,
    config.password,
    config.tls,
    config.tlsRejectUnauthorized,
    config.tlsCaPath,
    config.db,
    config.keyNamespace,
    config.connectTimeoutMs,
    config.clusterEndpoints,
    config.scaleReads,
  ]);
}

export function getRedisProvider(
  config?: RedisConnectionConfig,
  mode?: string,
): IRedisConnectionProvider {
  const resolvedConfig = config ?? redisConnectionConfigFromEnv();
  const resolvedMode = mode ?? process.env.REDIS_MODE ?? 'standalone';
  const key = fingerprint(resolvedConfig, resolvedMode);

  const existing = providerCache.get(key);
  if (existing) {
    return existing;
  }
  const provider = RedisConnectionProviderFactory.create(
    resolvedConfig,
    resolvedMode,
  );
  providerCache.set(key, provider);
  return provider;
}

/**
 * Close every provider this process built, releasing their connections.
 * Call once from application shutdown -- without it the shared clients (and,
 * on cluster, a socket to every node) stay open for the process's lifetime
 * and can keep the event loop alive past `stop()`.
 */
export async function closeAllRedisProviders(): Promise<void> {
  const providers = Array.from(providerCache.values());
  providerCache.clear();
  await Promise.all(
    providers.map(async (provider) => {
      try {
        await provider.close();
      } catch (error) {
        logger.warn('Error closing Redis provider during shutdown', { error });
      }
    }),
  );
}

/** Test-only: drop cached singleton providers between test cases. */
export function resetRedisProviderRegistry(): void {
  providerCache.clear();
}

// Self-registration: importing this factory module is enough for both OSS modes.
RedisConnectionProviderFactory.register(
  'standalone',
  (config) => new StandaloneRedisProvider(config),
);
RedisConnectionProviderFactory.register(
  'cluster',
  (config) => new ClusterRedisProvider(config),
);
