import { expect } from 'chai';

import { createIoredisCapture } from '../../../helpers/mock-ioredis-capture';
import { RedisConnectionConfig } from '../../../../src/libs/services/redis/connectionConfig';

const providerPath = require.resolve(
  '../../../../src/libs/services/redis/clusterRedisProvider',
);

function config(overrides: Partial<RedisConnectionConfig> = {}): RedisConnectionConfig {
  return {
    host: 'localhost',
    port: 6379,
    tls: false,
    tlsRejectUnauthorized: true,
    db: 0,
    keyNamespace: '',
    connectTimeoutMs: 10000,
    clusterEndpoints: [],
    scaleReads: 'master',
    ...overrides,
  };
}

describe('ClusterRedisProvider', () => {
  const capture = createIoredisCapture();
  let ClusterRedisProvider: typeof import('../../../../src/libs/services/redis/clusterRedisProvider').ClusterRedisProvider;

  beforeEach(() => {
    capture.install();
    delete require.cache[providerPath];
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    ClusterRedisProvider = require(providerPath).ClusterRedisProvider;
  });

  afterEach(() => {
    capture.restore();
    delete require.cache[providerPath];
  });

  describe('startup nodes', () => {
    it('uses cluster endpoints when set', () => {
      const provider = new ClusterRedisProvider(
        config({ clusterEndpoints: ['n1:7000', 'n2:7001'] }),
      );
      provider.createClient();
      const [startupNodes] = capture.capturedClusterArgs[0];
      expect(startupNodes).to.deep.equal([
        { host: 'n1', port: 7000 },
        { host: 'n2', port: 7001 },
      ]);
    });

    it('falls back to host/port when no endpoints are configured', () => {
      const provider = new ClusterRedisProvider(
        config({ host: 'h', port: 7000, clusterEndpoints: [] }),
      );
      provider.createClient();
      const [startupNodes] = capture.capturedClusterArgs[0];
      expect(startupNodes).to.deep.equal([{ host: 'h', port: 7000 }]);
    });
  });

  describe('scaleReads', () => {
    it('is forwarded to the Cluster options', () => {
      const provider = new ClusterRedisProvider(config({ scaleReads: 'all' }));
      provider.createClient();
      const [, clusterOptions] = capture.capturedClusterArgs[0];
      expect(clusterOptions.scaleReads).to.equal('all');
    });
  });

  describe('getClient caching', () => {
    it('returns the same client instance across calls', () => {
      const provider = new ClusterRedisProvider(config());
      const c1 = provider.getClient();
      const c2 = provider.getClient();
      expect(c1).to.equal(c2);
      expect(capture.capturedClusterArgs.length).to.equal(1);
    });
  });

  describe('createPubSubClient', () => {
    it('returns a dedicated connection, not one of the cluster client\'s nodes', () => {
      const provider = new ClusterRedisProvider(config());
      const clusterClient = provider.getClient() as any;
      const node = provider.createPubSubClient();

      expect(node).to.not.equal(undefined);
      // SUBSCRIBE puts a connection into subscriber mode where it can no
      // longer serve ordinary commands, so handing back one of the shared
      // cluster client's own node connections would break the cluster client.
      expect(clusterClient.nodes('master')).to.not.include(node);
      expect(capture.capturedRedisArgs.length).to.equal(1);
    });
  });

  describe('scanKeys', () => {
    it('fans out over every master node', async () => {
      const provider = new ClusterRedisProvider(config());
      const client = provider.getClient() as any;
      const nodeA = { scan: () => Promise.resolve(['0', ['a1']]) };
      const nodeB = { scan: () => Promise.resolve(['0', ['b1']]) };
      client.nodes = () => [nodeA, nodeB];

      const keys: string[] = [];
      for await (const key of provider.scanKeys('*')) {
        keys.push(key);
      }
      expect(keys.sort()).to.deep.equal(['a1', 'b1']);
    });

    it('pings to load the slot map before enumerating masters', async () => {
      const provider = new ClusterRedisProvider(config());
      const client = provider.getClient() as any;
      client.status = 'connecting';
      client.nodes = () => [{ scan: () => Promise.resolve(['0', ['k']]) }];

      for await (const _key of provider.scanKeys('*')) {
        // drain
      }

      expect(client.ping.called).to.equal(true);
    });

    it('throws rather than reporting an empty keyspace when no master is reachable', async () => {
      // `nodes('master')` is empty until ioredis has loaded the slot map. A
      // fan-out over `[]` would silently look like "the keyspace is empty",
      // which is how a partial SCAN result gets mistaken for a real answer.
      const provider = new ClusterRedisProvider(config());
      const client = provider.getClient() as any;
      client.nodes = () => [];

      try {
        for await (const _key of provider.scanKeys('*')) {
          // unreachable
        }
        expect.fail('Should have thrown');
      } catch (error) {
        expect((error as Error).message).to.include('no reachable primaries');
      }
    });
  });

  describe('loadScript', () => {
    it('loads on every master and returns the last sha', async () => {
      const provider = new ClusterRedisProvider(config());
      const client = provider.getClient() as any;
      const nodeA = { script: () => Promise.resolve('sha1') };
      const nodeB = { script: () => Promise.resolve('sha1') };
      client.nodes = () => [nodeA, nodeB];

      const sha = await provider.loadScript('return 1');
      expect(sha).to.equal('sha1');
    });

    it('throws when there are no reachable primaries', async () => {
      const provider = new ClusterRedisProvider(config());
      const client = provider.getClient() as any;
      client.nodes = () => [];

      let threw = false;
      try {
        await provider.loadScript('return 1');
      } catch {
        threw = true;
      }
      expect(threw).to.equal(true);
    });
  });

  describe('keySlot', () => {
    it('returns a valid Redis Cluster slot', () => {
      const provider = new ClusterRedisProvider(config());
      const slot = provider.keySlot('some-key');
      expect(slot).to.be.at.least(0);
      expect(slot).to.be.below(16384);
    });

    it('maps keys sharing a hashtag to the same slot', () => {
      const provider = new ClusterRedisProvider(config());
      expect(provider.keySlot('{tenant-a}:foo')).to.equal(
        provider.keySlot('{tenant-a}:bar'),
      );
    });
  });

  describe('connectionUrl', () => {
    it('throws because a cluster has no single connection url', () => {
      const provider = new ClusterRedisProvider(config());
      expect(() => provider.connectionUrl()).to.throw();
    });
  });

  describe('ping', () => {
    it('returns false when the client throws', async () => {
      const provider = new ClusterRedisProvider(config());
      const client = provider.getClient() as any;
      client.ping = () => Promise.reject(new Error('down'));
      expect(await provider.ping()).to.equal(false);
    });
  });

  describe('close', () => {
    it('quits every client it created', async () => {
      const provider = new ClusterRedisProvider(config());
      const c1 = provider.createClient() as any;
      const c2 = provider.createClient() as any;

      await provider.close();

      expect(c1.quit.calledOnce).to.equal(true);
      expect(c2.quit.calledOnce).to.equal(true);
    });
  });

  describe('mode/isCluster', () => {
    it('reports cluster mode', () => {
      const provider = new ClusterRedisProvider(config());
      expect(provider.isCluster).to.equal(true);
      expect(provider.mode).to.equal('cluster');
    });
  });
});
