import { expect } from 'chai';
import type { Redis } from 'ioredis';

import { createIoredisCapture } from '../../../helpers/mock-ioredis-capture';
import {
  RedisConnectionConfig,
  redisConnectionConfigFromEnv,
} from '../../../../src/libs/services/redis/connectionConfig';
import {
  IRedisConnectionProvider,
  RedisClient,
} from '../../../../src/libs/services/redis/connectionProvider.interface';
import {
  RedisConnectionProviderFactory,
  getRedisProvider,
  resetRedisProviderRegistry,
} from '../../../../src/libs/services/redis/connectionProviderFactory';

function config(overrides: Partial<RedisConnectionConfig> = {}): RedisConnectionConfig {
  return { ...redisConnectionConfigFromEnv(), host: 'h', db: 0, ...overrides };
}

// This test only exercises factory registration/caching/dispatch, never a
// real client, so the stub client is an empty object cast to the interface's
// declared return types (not `any`) -- that keeps a future incompatible
// change to `IRedisConnectionProvider`'s signatures caught here too.
class FakeProvider implements IRedisConnectionProvider {
  readonly isCluster = false;
  readonly mode = 'fake';
  get keyNamespace(): string {
    return this.config.keyNamespace;
  }
  constructor(public readonly config: RedisConnectionConfig) {}
  getClient(): RedisClient { return {} as RedisClient; }
  createClient(): RedisClient { return {} as RedisClient; }
  createPubSubClient(): Redis { return {} as Redis; }
  release(): void { /* no-op */ }
  async *scanKeys(): AsyncIterable<string> { /* empty */ }
  async loadScript(): Promise<string> { return 'sha'; }
  keySlot(): number { return 0; }
  connectionUrl(): string { return 'redis://fake'; }
  async ping(): Promise<boolean> { return true; }
  async close(): Promise<void> {}
}

describe('RedisConnectionProviderFactory', () => {
  const capture = createIoredisCapture();
  let savedRedisMode: string | undefined;

  beforeEach(() => {
    capture.install();
    resetRedisProviderRegistry();
    // These assert the factory's own defaults, so they must not read the
    // ambient REDIS_MODE -- the cluster integration job runs the whole suite
    // with REDIS_MODE=cluster set.
    savedRedisMode = process.env.REDIS_MODE;
    delete process.env.REDIS_MODE;
  });

  afterEach(() => {
    capture.restore();
    resetRedisProviderRegistry();
    RedisConnectionProviderFactory.resetForTests(['fake']);
    if (savedRedisMode === undefined) {
      delete process.env.REDIS_MODE;
    } else {
      process.env.REDIS_MODE = savedRedisMode;
    }
  });

  describe('create', () => {
    it('defaults to standalone mode', () => {
      const provider = RedisConnectionProviderFactory.create(config());
      expect(provider.mode).to.equal('standalone');
    });

    it('throws for an unknown mode', () => {
      expect(() =>
        RedisConnectionProviderFactory.create(config(), 'memorydb'),
      ).to.throw(/Unknown REDIS_MODE/);
    });

    it('uses a registered mode', () => {
      RedisConnectionProviderFactory.register(
        'fake',
        (c) => new FakeProvider(c),
      );
      const provider = RedisConnectionProviderFactory.create(config(), 'fake');
      expect(provider).to.be.instanceOf(FakeProvider);
    });

    it('rejects a non-zero db outside standalone mode', () => {
      RedisConnectionProviderFactory.register(
        'fake',
        (c) => new FakeProvider(c),
      );
      expect(() =>
        RedisConnectionProviderFactory.create(config({ db: 1 }), 'fake'),
      ).to.throw(/REDIS_DB is not supported/);
    });

    it('allows a non-zero db in standalone mode', () => {
      const provider = RedisConnectionProviderFactory.create(
        config({ db: 1 }),
        'standalone',
      );
      expect(provider.mode).to.equal('standalone');
    });

    it('registers both OSS defaults', () => {
      const modes = RedisConnectionProviderFactory.registeredModes();
      expect(modes).to.include('standalone');
      expect(modes).to.include('cluster');
    });
  });

  describe('getRedisProvider singleton', () => {
    it('returns the same instance for the same config and mode', () => {
      const cfg = config({ host: 'h', port: 1 });
      const p1 = getRedisProvider(cfg, 'standalone');
      const p2 = getRedisProvider(cfg, 'standalone');
      expect(p1).to.equal(p2);
    });

    it('returns a different instance for a different host', () => {
      const p1 = getRedisProvider(config({ host: 'h1' }), 'standalone');
      const p2 = getRedisProvider(config({ host: 'h2' }), 'standalone');
      expect(p1).to.not.equal(p2);
    });

    it('returns a different instance for a different mode', () => {
      RedisConnectionProviderFactory.register(
        'fake',
        (c) => new FakeProvider(c),
      );
      const cfg = config({ host: 'h' });
      const p1 = getRedisProvider(cfg, 'standalone');
      const p2 = getRedisProvider(cfg, 'fake');
      expect(p1).to.not.equal(p2);
    });

    it('forces a new instance after resetRedisProviderRegistry', () => {
      const cfg = config({ host: 'h' });
      const p1 = getRedisProvider(cfg, 'standalone');
      resetRedisProviderRegistry();
      const p2 = getRedisProvider(cfg, 'standalone');
      expect(p1).to.not.equal(p2);
    });
  });

  describe('ensureProviderModuleLoaded (R10)', () => {
    const originalEnv = process.env.REDIS_PROVIDER_MODULE;

    afterEach(() => {
      if (originalEnv === undefined) {
        delete process.env.REDIS_PROVIDER_MODULE;
      } else {
        process.env.REDIS_PROVIDER_MODULE = originalEnv;
      }
      RedisConnectionProviderFactory.resetForTests(['fake']);
    });

    it('is a no-op when REDIS_PROVIDER_MODULE is unset', async () => {
      delete process.env.REDIS_PROVIDER_MODULE;
      await RedisConnectionProviderFactory.ensureProviderModuleLoaded();
      // No throw is the assertion; nothing to import, nothing to register.
    });

    it('does not throw when the configured module cannot be imported', async () => {
      process.env.REDIS_PROVIDER_MODULE = '@pipeshub-ee/does-not-exist';
      await RedisConnectionProviderFactory.ensureProviderModuleLoaded();
      // Logged, not thrown -- a missing/misconfigured EE module must not
      // crash OSS startup.
    });

    it('only attempts to import a given module once per process', async () => {
      process.env.REDIS_PROVIDER_MODULE = '@pipeshub-ee/does-not-exist-2';
      await RedisConnectionProviderFactory.ensureProviderModuleLoaded();
      await RedisConnectionProviderFactory.ensureProviderModuleLoaded();
      // Second call returns immediately via the discovered-modules guard;
      // no separate assertion point since both calls resolve without throwing.
    });
  });
});

describe('credentials over unverified TLS', () => {
  // TLS with verification off is encrypted but *unauthenticated*: a MITM can
  // present any cert, terminate the session, and harvest the password. The
  // guard lives in the factory so an EE-registered provider is covered too.
  const withTls = (over: Partial<RedisConnectionConfig>): RedisConnectionConfig => ({
    host: 'localhost',
    port: 6379,
    tls: true,
    tlsRejectUnauthorized: true,
    db: 0,
    keyNamespace: '',
    connectTimeoutMs: 10000,
    clusterEndpoints: [],
    scaleReads: 'master',
    ...over,
  });

  it('refuses a password when verification is disabled', () => {
    expect(() =>
      RedisConnectionProviderFactory.create(
        withTls({ tlsRejectUnauthorized: false, password: 'secret' }),
        'standalone',
      ),
    ).to.throw(/REDIS_TLS_REJECT_UNAUTHORIZED=false with Redis credentials/);
  });

  it('refuses a username when verification is disabled', () => {
    expect(() =>
      RedisConnectionProviderFactory.create(
        withTls({ tlsRejectUnauthorized: false, username: 'acl-user' }),
        'standalone',
      ),
    ).to.throw(/REDIS_TLS_REJECT_UNAUTHORIZED=false with Redis credentials/);
  });

  it('allows credentials when verification is on', () => {
    expect(() =>
      RedisConnectionProviderFactory.create(
        withTls({ password: 'secret' }),
        'standalone',
      ),
    ).to.not.throw();
  });

  it('allows unverified TLS when there are no credentials to leak', () => {
    expect(() =>
      RedisConnectionProviderFactory.create(
        withTls({ tlsRejectUnauthorized: false }),
        'standalone',
      ),
    ).to.not.throw();
  });

  // Deliberate, and repeatedly re-raised in review -- so the reasoning lives
  // here rather than in a PR thread.
  //
  // Requiring TLS whenever a password is set would break every default
  // install: Compose and Helm both ship `REDIS_PASSWORD` with TLS off. That
  // posture is not Redis-specific -- in the same compose file MongoDB
  // (`mongodb://user:pass@mongodb:27017`), Neo4j (`bolt://`, not `bolt+s://`)
  // and Qdrant (plain gRPC + API key) all send credentials the same way, and
  // none of the four publishes a port to the host: they are reachable only on
  // the Docker bridge / cluster network. Singling Redis out would be
  // inconsistent without improving anything (an attacker who can read that
  // network reads the Mongo and Neo4j passwords too), and applying it to all
  // four would require every user to provision a PKI before `docker compose
  // up`. That is a product decision, not a review fix.
  //
  // What IS enforced, because it has no legitimate reading:
  // TLS on + verification off + credentials -> rejected (see above).
  it('leaves the default install (no TLS, password set) alone', () => {
    expect(() =>
      RedisConnectionProviderFactory.create(
        withTls({ tls: false, password: 'secret' }),
        'standalone',
      ),
    ).to.not.throw();
  });
});
