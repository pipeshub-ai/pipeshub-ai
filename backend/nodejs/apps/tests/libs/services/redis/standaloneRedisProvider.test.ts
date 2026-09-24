import { expect } from 'chai';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

import {
  createIoredisCapture,
  FakeClientHandle,
} from '../../../helpers/mock-ioredis-capture';
import { RedisConnectionConfig } from '../../../../src/libs/services/redis/connectionConfig';

const providerPath = require.resolve(
  '../../../../src/libs/services/redis/standaloneRedisProvider',
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

describe('StandaloneRedisProvider', () => {
  const capture = createIoredisCapture();
  let StandaloneRedisProvider: typeof import('../../../../src/libs/services/redis/standaloneRedisProvider').StandaloneRedisProvider;

  beforeEach(() => {
    capture.install();
    delete require.cache[providerPath];
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    StandaloneRedisProvider = require(providerPath).StandaloneRedisProvider;
  });

  afterEach(() => {
    capture.restore();
    delete require.cache[providerPath];
  });

  it('creates a fresh client on every createClient call', () => {
    const provider = new StandaloneRedisProvider(config());
    provider.createClient();
    provider.createClient();
    expect(capture.capturedRedisArgs.length).to.equal(2);
  });

  it('caches the shared client returned by getClient', () => {
    const provider = new StandaloneRedisProvider(config());
    const c1 = provider.getClient();
    const c2 = provider.getClient();
    expect(c1).to.equal(c2);
    expect(capture.capturedRedisArgs.length).to.equal(1);
  });

  it('passes username/password through to the client options', () => {
    const provider = new StandaloneRedisProvider(
      config({ username: 'u', password: 'p' }),
    );
    provider.createClient();
    const [options] = capture.capturedRedisArgs[0];
    expect(options.username).to.equal('u');
    expect(options.password).to.equal('p');
  });

  it('sets tls options when tls is enabled', () => {
    const provider = new StandaloneRedisProvider(
      config({ tls: true, tlsRejectUnauthorized: false }),
    );
    provider.createClient();
    const [options] = capture.capturedRedisArgs[0];
    expect(options.tls).to.deep.include({ rejectUnauthorized: false });
  });

  it('disables maxRetriesPerRequest for blocking clients', () => {
    const provider = new StandaloneRedisProvider(config());
    provider.createClient({ blocking: true });
    const [options] = capture.capturedRedisArgs[0];
    expect(options.maxRetriesPerRequest).to.equal(null);
  });

  describe('connectionUrl', () => {
    it('formats a plain no-auth url', () => {
      const provider = new StandaloneRedisProvider(
        config({ host: 'h', port: 1234, db: 2 }),
      );
      expect(provider.connectionUrl()).to.equal('redis://h:1234/2');
    });

    it('formats a password-only url', () => {
      const provider = new StandaloneRedisProvider(
        config({ host: 'h', port: 1234, password: 'pw' }),
      );
      expect(provider.connectionUrl()).to.equal('redis://:pw@h:1234/0');
    });

    it('formats a username+password url', () => {
      const provider = new StandaloneRedisProvider(
        config({ host: 'h', port: 1234, username: 'u', password: 'pw' }),
      );
      expect(provider.connectionUrl()).to.equal('redis://u:pw@h:1234/0');
    });

    it('uses rediss scheme when tls is enabled', () => {
      const provider = new StandaloneRedisProvider(
        config({ host: 'h', port: 1234, tls: true }),
      );
      expect(provider.connectionUrl().startsWith('rediss://')).to.equal(true);
    });
  });

  describe('TLS', () => {
    it('passes the CA file\'s contents, not its path', () => {
      // Node's TLS `ca` option parses the value as PEM. Handing it a path
      // finds no certificate in that string and silently falls back to the
      // default trust store, so a private-CA endpoint fails verification for
      // a reason nothing logs. (redis-py's `ssl_ca_certs` does take a path,
      // which is why only the Node side needs this.)
      const caPath = path.join(os.tmpdir(), `redis-ca-${process.pid}.pem`);
      fs.writeFileSync(caPath, '-----BEGIN CERTIFICATE-----\nfake\n');
      try {
        const provider = new StandaloneRedisProvider(
          config({ tls: true, tlsCaPath: caPath }),
        );
        provider.getClient();
        const [options] = capture.capturedRedisArgs[0];
        expect(Buffer.isBuffer(options.tls.ca)).to.equal(true);
        expect(options.tls.ca.toString()).to.include('BEGIN CERTIFICATE');
      } finally {
        fs.unlinkSync(caPath);
      }
    });

    it('fails at construction when the CA path cannot be read', () => {
      expect(
        () =>
          new StandaloneRedisProvider(
            config({ tls: true, tlsCaPath: '/nonexistent/redis-ca.pem' }),
          ),
      ).to.throw(/REDIS_TLS_CA_PATH/);
    });

    it('omits ca entirely when no path is configured', () => {
      const provider = new StandaloneRedisProvider(config({ tls: true }));
      provider.getClient();
      const [options] = capture.capturedRedisArgs[0];
      expect(options.tls.ca).to.equal(undefined);
      expect(options.tls.rejectUnauthorized).to.equal(true);
    });
  });

  describe('scanKeys', () => {
    it('paginates through cursors until it returns to 0', async () => {
      const provider = new StandaloneRedisProvider(config());
      const client = provider.getClient() as unknown as FakeClientHandle;
      client.scan
        .onCall(0)
        .resolves(['5', ['k1']])
        .onCall(1)
        .resolves(['0', ['k2']]);

      const keys: string[] = [];
      for await (const key of provider.scanKeys('k:*')) {
        keys.push(key);
      }
      expect(keys).to.deep.equal(['k1', 'k2']);
    });
  });

  describe('loadScript', () => {
    it('returns the sha from SCRIPT LOAD', async () => {
      const provider = new StandaloneRedisProvider(config());
      const client = provider.getClient() as unknown as FakeClientHandle;
      client.script.resolves('deadbeef');

      const sha = await provider.loadScript('return 1');
      expect(sha).to.equal('deadbeef');
    });
  });

  describe('keySlot', () => {
    it('is always 0', () => {
      const provider = new StandaloneRedisProvider(config());
      expect(provider.keySlot('any-key')).to.equal(0);
      expect(provider.keySlot('{tag}other')).to.equal(0);
    });
  });

  describe('ping', () => {
    it('returns true when the client responds PONG', async () => {
      const provider = new StandaloneRedisProvider(config());
      expect(await provider.ping()).to.equal(true);
    });

    it('returns false when the client throws', async () => {
      const provider = new StandaloneRedisProvider(config());
      const client = provider.getClient() as any;
      client.ping.rejects(new Error('down'));
      expect(await provider.ping()).to.equal(false);
    });
  });

  describe('close', () => {
    it('quits every client it created', async () => {
      const provider = new StandaloneRedisProvider(config());
      const c1 = provider.createClient() as any;
      const c2 = provider.createClient() as any;

      await provider.close();

      expect(c1.quit.calledOnce).to.equal(true);
      expect(c2.quit.calledOnce).to.equal(true);
    });
  });

  describe('mode/isCluster', () => {
    it('reports standalone, non-cluster', () => {
      const provider = new StandaloneRedisProvider(config());
      expect(provider.isCluster).to.equal(false);
      expect(provider.mode).to.equal('standalone');
    });
  });
});
