/**
 * The scheduler and the worker address the BullMQ queue by
 * `<prefix>:<queue name>`, so a prefix mismatch leaves the worker listening
 * on a queue nothing writes to — with no error anywhere. `crawling_queue.ts`
 * exists so that derivation lives in one place; these pin it.
 */
import { expect } from 'chai';

import {
  crawlingQueuePrefix,
  crawlingRedisProvider,
} from '../../../../src/modules/crawling_manager/services/crawling_queue';
import { IRedisConnectionProvider } from '../../../../src/libs/services/redis/connectionProvider.interface';
import { resetRedisProviderRegistry } from '../../../../src/libs/services/redis/connectionProviderFactory';

function providerWithNamespace(keyNamespace: string): IRedisConnectionProvider {
  return { keyNamespace } as IRedisConnectionProvider;
}

describe('crawling queue wiring', () => {
  let savedRedisMode: string | undefined;

  beforeEach(() => {
    // `crawlingRedisProvider` resolves the mode from the environment, and
    // these assert standalone behaviour -- the redis-cluster CI job runs the
    // whole suite with REDIS_MODE=cluster set.
    savedRedisMode = process.env.REDIS_MODE;
    delete process.env.REDIS_MODE;
    resetRedisProviderRegistry();
  });

  afterEach(() => {
    resetRedisProviderRegistry();
    delete process.env.REDIS_KEY_NAMESPACE;
    if (savedRedisMode === undefined) {
      delete process.env.REDIS_MODE;
    } else {
      process.env.REDIS_MODE = savedRedisMode;
    }
  });

  describe('crawlingQueuePrefix', () => {
    it('is hash-tagged so every queue key lands in one cluster slot', () => {
      // BullMQ's job-state transitions are Lua scripts touching several keys
      // per queue; without a shared hash tag they are a CROSSSLOT error on
      // Redis Cluster / MemoryDB.
      const prefix = crawlingQueuePrefix(providerWithNamespace(''));
      expect(prefix).to.equal('{crawling}');
      expect(prefix.startsWith('{')).to.equal(true);
      expect(prefix.endsWith('}')).to.equal(true);
    });

    it('puts the namespace inside the braces, not outside them', () => {
      // Outside the braces the tag would still be `{crawling}` for every
      // deployment, so two releases sharing one endpoint would collide on
      // one slot instead of being isolated.
      const prefix = crawlingQueuePrefix(providerWithNamespace('tenant-a'));
      expect(prefix).to.equal('{tenant-a-crawling}');
      const tag = prefix.slice(prefix.indexOf('{') + 1, prefix.indexOf('}'));
      expect(tag).to.equal('tenant-a-crawling');
    });

    it('gives two namespaces two different tags', () => {
      expect(crawlingQueuePrefix(providerWithNamespace('a'))).to.not.equal(
        crawlingQueuePrefix(providerWithNamespace('b')),
      );
    });
  });

  describe('crawlingRedisProvider', () => {
    it('forwards the stored TLS flag into the connection config', () => {
      // The admin UI stores `tls` on the Redis config; an install that
      // enabled it there has no REDIS_TLS_ENABLED set, and dropping the flag
      // would connect the crawling queue in plaintext.
      const provider = crawlingRedisProvider({
        host: 'localhost',
        port: 6379,
        tls: true,
      });
      expect(provider.connectionUrl().startsWith('rediss://')).to.equal(true);
    });

    it('reuses one provider for the same connection', () => {
      const config = { host: 'localhost', port: 6379 };
      expect(crawlingRedisProvider(config)).to.equal(
        crawlingRedisProvider(config),
      );
    });
  });
});
