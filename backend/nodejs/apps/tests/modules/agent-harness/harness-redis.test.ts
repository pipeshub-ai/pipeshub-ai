import { expect } from 'chai';

import { createIoredisCapture } from '../../helpers/mock-ioredis-capture';
import { resetRedisProviderRegistry } from '../../../src/libs/services/redis/connectionProviderFactory';
import { harnessRedisProvider } from '../../../src/modules/agent-harness/adapters/redis/harness-redis';

// WP88-FU-1: the worker publishes a run's live frames to the configured Redis whole, `REDIS_DB` included, so Node
// must follow the same database or every answer reaches the browser at once from the journal.
describe('harnessRedisProvider (WP88-FU-1)', () => {
  const capture = createIoredisCapture();
  let savedMode: string | undefined;

  beforeEach(() => {
    savedMode = process.env.REDIS_MODE;
    delete process.env.REDIS_MODE;
    capture.install();
    resetRedisProviderRegistry();
  });

  afterEach(() => {
    capture.restore();
    resetRedisProviderRegistry();
    if (savedMode === undefined) delete process.env.REDIS_MODE;
    else process.env.REDIS_MODE = savedMode;
  });

  it('follows the configured database, not DB 0', () => {
    const provider = harnessRedisProvider({
      host: 'redis.local',
      port: 6380,
      db: 3,
    });
    expect(provider.connectionUrl()).to.equal('redis://redis.local:6380/3');
  });

  it('keeps the configured credentials and TLS', () => {
    const provider = harnessRedisProvider({
      host: 'redis.local',
      port: 6380,
      username: 'svc',
      password: 'pw',
      db: 2,
      tls: true,
    });
    expect(provider.connectionUrl()).to.equal(
      'rediss://svc:pw@redis.local:6380/2',
    );
  });
});
