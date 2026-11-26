import { expect } from 'chai';
import net from 'node:net';
import type { AddressInfo } from 'node:net';
import {
  configuredStoreAuthFindings,
  enforceStoreAuth,
  qdrantAnonymousProbe,
  redisAnonymousProbe,
  StoreAuthRefused,
  storeAuthFindings,
  storeAuthMode,
} from '../../../src/libs/config/store-auth';
import { reconcileKafkaLogin } from '../../../src/modules/tokens_manager/services/cm.service';
import { etcdCredentials, etcdTlsFromEnv } from '../../../src/libs/keyValueStore/etcd-tls';

/** SEC-D-06: the app refuses a store that accepts clients without a credential (store-auth.ts). */

const COMPLETE = {
  // The hardened profile (ADR-OPS-07); the compatibility cases drop it.
  PIPESHUB_HARDENED: 'true',
  QDRANT_API_KEY: 'q'.repeat(40),
  MONGO_URI: 'mongodb://admin:pw@mongodb:27017/?authSource=admin',
  DATA_STORE: 'neo4j',
  NEO4J_PASSWORD: 'n'.repeat(32),
  KV_STORE_TYPE: 'redis',
  MESSAGE_BROKER: 'redis',
};

/** A one-reply Redis: answers every connection's first line with `reply`. */
async function fakeRedis(reply: string): Promise<{ port: number; close: () => Promise<void> }> {
  const server = net.createServer((socket) => {
    socket.once('data', () => socket.write(reply));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    port: (server.address() as AddressInfo).port,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}

const quiet = { warn: () => undefined };

describe('libs/config/store-auth (SEC-D-06)', () => {
  describe('configuration', () => {
    it('a complete configuration has no finding', () => {
      expect(configuredStoreAuthFindings(COMPLETE)).to.deep.equal([]);
    });

    it('names the variable to set for each active store that lacks its credential', () => {
      const stores = configuredStoreAuthFindings({
        ...COMPLETE,
        QDRANT_API_KEY: '  ',
        MONGO_URI: 'mongodb://mongodb:27017/es',
        NEO4J_PASSWORD: '',
        KV_STORE_TYPE: 'etcd',
        ETCD_URL: 'http://etcd:2379',
        MESSAGE_BROKER: 'kafka',
        KAFKA_USERNAME: 'pipeshub',
      }).map((f) => [f.store, f.problem.split(' ')[0]]);
      expect(stores).to.deep.equal([
        ['qdrant', 'QDRANT_API_KEY'],
        ['mongodb', 'MONGO_URI'],
        ['neo4j', 'NEO4J_PASSWORD'],
        ['etcd', 'etcd'],
        ['kafka', 'Kafka'],
      ]);
    });

    it('checks only the stores the deployment uses', () => {
      expect(configuredStoreAuthFindings({ ...COMPLETE, DATA_STORE: 'arangodb', ARANGO_PASSWORD: 'a'.repeat(20), NEO4J_PASSWORD: '' })).to.deep.equal([]);
      expect(configuredStoreAuthFindings({ ...COMPLETE, VECTOR_DB_TYPE: 'opensearch', QDRANT_API_KEY: '' })).to.deep.equal([]);
      expect(configuredStoreAuthFindings({ ...COMPLETE, DATA_STORE: 'ArangoDB' }).map((f) => f.store)).to.deep.equal(['arangodb']);
    });

    it('a Mongo URI with an empty password or user is refused; an auth mechanism without a password is not', () => {
      for (const uri of ['mongodb://admin:@mongodb:27017/', 'mongodb://:pw@mongodb:27017/', 'mongodb://admin@mongodb/', 'mongodb+srv://cluster.example.net/db']) {
        expect(configuredStoreAuthFindings({ ...COMPLETE, MONGO_URI: uri }).map((f) => f.store), uri).to.deep.equal(['mongodb']);
      }
      for (const uri of ['mongodb://cluster.example.net/?authMechanism=MONGODB-X509', 'mongodb+srv://u:p%40ss@cluster.example.net/db']) {
        expect(configuredStoreAuthFindings({ ...COMPLETE, MONGO_URI: uri }), uri).to.deep.equal([]);
      }
    });

    it('etcd needs https and all three TLS files; a case variant of the scheme is still https', () => {
      const tlsFiles = { ETCD_CA_FILE: '/tls/ca.crt', ETCD_CERT_FILE: '/tls/client.crt', ETCD_KEY_FILE: '/tls/client.key' };
      expect(configuredStoreAuthFindings({ ...COMPLETE, KV_STORE_TYPE: 'etcd', ETCD_URL: 'HTTPS://etcd:2379', ...tlsFiles })).to.deep.equal([]);
      expect(configuredStoreAuthFindings({ ...COMPLETE, KV_STORE_TYPE: 'etcd', ETCD_URL: 'https://etcd:2379', ...tlsFiles, ETCD_KEY_FILE: '' }).map((f) => f.store)).to.deep.equal(['etcd']);
      expect(configuredStoreAuthFindings({ ...COMPLETE, KV_STORE_TYPE: 'etcd', ETCD_URL: 'http://https.example:2379', ...tlsFiles }).map((f) => f.store)).to.deep.equal(['etcd']);
    });

    it('on the hardened profile only an explicit warn relaxes the check; anything else enforces', () => {
      expect(storeAuthMode({ STORE_AUTH_CHECK: ' WARN ', PIPESHUB_HARDENED: 'true' })).to.equal('warn');
      for (const value of [undefined, '', 'off', 'false', 'disabled', 'warning', '0']) {
        expect(storeAuthMode({ STORE_AUTH_CHECK: value, PIPESHUB_HARDENED: 'true' }), String(value)).to.equal('enforce');
      }
    });

    it('R51-COMPAT: unset warns on the compatibility profile, as a release whose stores had no login ran; enforce still enforces', () => {
      expect(storeAuthMode({})).to.equal('warn');
      expect(storeAuthMode({ STORE_AUTH_CHECK: '' })).to.equal('warn');
      expect(storeAuthMode({ STORE_AUTH_CHECK: 'enforce' })).to.equal('enforce');
      expect(storeAuthMode({ STORE_AUTH_CHECK: 'warn', PIPESHUB_HARDENED: 'true' })).to.equal('warn');
    });
  });

  describe('anonymous probes', () => {
    it('Redis answering PONG without a password is an open store', async () => {
      const redis = await fakeRedis('+PONG\r\n');
      try {
        expect(await redisAnonymousProbe({ REDIS_HOST: '127.0.0.1', REDIS_PORT: String(redis.port) })()).to.equal(true);
      } finally {
        await redis.close();
      }
    });

    it('Redis refusing with NOAUTH (requirepass) or NOPERM (ACL default user off) is closed', async () => {
      for (const reply of ['-NOAUTH Authentication required.\r\n', '-NOPERM this user has no permissions\r\n']) {
        const redis = await fakeRedis(reply);
        try {
          expect(await redisAnonymousProbe({ REDIS_HOST: '127.0.0.1', REDIS_PORT: String(redis.port) })(), reply).to.equal(false);
        } finally {
          await redis.close();
        }
      }
    });

    it('a reply split across packets is still read whole', async () => {
      const server = net.createServer((socket) => {
        socket.once('data', () => {
          socket.write('+PO');
          setTimeout(() => socket.write('NG\r\n'), 20);
        });
      });
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
      try {
        const port = (server.address() as AddressInfo).port;
        expect(await redisAnonymousProbe({ REDIS_HOST: '127.0.0.1', REDIS_PORT: String(port) })()).to.equal(true);
      } finally {
        await new Promise((resolve) => server.close(resolve));
      }
    });

    it('an unreachable Redis or an unknown reply is unknown, not open: the client reports that itself', async () => {
      const redis = await fakeRedis('-ERR unknown\r\n');
      const { port } = redis;
      try {
        expect(await redisAnonymousProbe({ REDIS_HOST: '127.0.0.1', REDIS_PORT: String(port) })()).to.equal(null);
      } finally {
        await redis.close();
      }
      expect(await redisAnonymousProbe({ REDIS_HOST: '127.0.0.1', REDIS_PORT: String(port) })()).to.equal(null);
    });

    it('Qdrant: 200 without a key is open, 401/403 closed, anything else unknown', async () => {
      const answer = (status: number) => (async () => new Response('{}', { status })) as unknown as typeof fetch;
      expect(await qdrantAnonymousProbe({}, answer(200))()).to.equal(true);
      expect(await qdrantAnonymousProbe({}, answer(401))()).to.equal(false);
      expect(await qdrantAnonymousProbe({}, answer(403))()).to.equal(false);
      expect(await qdrantAnonymousProbe({}, answer(503))()).to.equal(null);
      const failing = (async () => { throw new Error('ECONNREFUSED'); }) as unknown as typeof fetch;
      expect(await qdrantAnonymousProbe({}, failing)()).to.equal(null);
    });

    it('the probe sends no credential, even when the app has one configured', async () => {
      let sent: RequestInit | undefined;
      const capture = (async (_url: string, init?: RequestInit) => {
        sent = init;
        return new Response('{}', { status: 401 });
      }) as unknown as typeof fetch;
      await qdrantAnonymousProbe({ QDRANT_API_KEY: 'k'.repeat(30) }, capture)();
      expect(JSON.stringify(sent?.headers ?? {})).to.not.include('k'.repeat(30));
    });
  });

  describe('enforcement', () => {
    const open = async () => true;
    const closed = async () => false;

    it('an open Redis refuses start with what to set, and never prints a credential', async () => {
      const env = { ...COMPLETE, REDIS_PASSWORD: 'secret-redis-value' };
      let thrown: unknown;
      try {
        await enforceStoreAuth(env, quiet, { redis: open, qdrant: closed });
      } catch (error) {
        thrown = error;
      }
      expect(thrown).to.be.instanceOf(StoreAuthRefused);
      const message = (thrown as Error).message;
      expect(message).to.include('redis: Redis answered a PING without a password');
      expect(message).to.include('STORE_AUTH_CHECK=warn');
      expect(message).to.include('./install.sh --upgrade');
      expect(message).to.not.include('secret-redis-value');
    });

    it('R51-COMPAT: on the compatibility profile an open Redis starts with the finding logged', async () => {
      const logged: Record<string, unknown>[] = [];
      const compat: Partial<typeof COMPLETE> = { ...COMPLETE };
      delete compat.PIPESHUB_HARDENED;
      const findings = await enforceStoreAuth(
        { ...compat, REDIS_PASSWORD: '' },
        { warn: (_m, meta) => logged.push(meta ?? {}) },
        { redis: open, qdrant: closed },
      );
      expect(findings.map((f) => f.store)).to.include('redis');
      expect(logged.length).to.be.greaterThan(0);
    });

    it('warn starts and logs each finding once', async () => {
      const logged: Record<string, unknown>[] = [];
      const findings = await enforceStoreAuth(
        { ...COMPLETE, STORE_AUTH_CHECK: 'warn', QDRANT_API_KEY: '' },
        { warn: (_m, meta) => logged.push(meta ?? {}) },
        { redis: open, qdrant: open },
      );
      expect(findings.map((f) => f.store)).to.deep.equal(['qdrant', 'redis']);
      expect(logged.map((m) => m.store)).to.deep.equal(['qdrant', 'redis']);
    });

    it('closed and unknown stores pass', async () => {
      expect(await storeAuthFindings(COMPLETE, { redis: closed, qdrant: async () => null })).to.deep.equal([]);
    });
  });

  describe('reconcileKafkaLogin: the environment login wins for its own brokers', () => {
    const login = { mechanism: 'scram-sha-512' as const, username: 'pipeshub', password: 'p1' };
    const env = { brokers: ['kafka-1:9092'], ssl: false, sasl: login };

    it('a config stored before the broker required SCRAM moves onto the login', () => {
      expect(reconcileKafkaLogin({ brokers: ['kafka-1:9092'], ssl: false }, env)).to.deep.equal(env);
    });

    it('a rotated KAFKA_PASSWORD replaces the stored one', () => {
      const stored = { brokers: [' kafka-1:9092'], ssl: false, sasl: { ...login, password: 'old' } };
      expect(reconcileKafkaLogin(stored, env)?.sasl?.password).to.equal('p1');
    });

    it('an admin\'s external brokers, or an environment without a login, are left as stored', () => {
      expect(reconcileKafkaLogin({ brokers: ['b-1.msk.example:9096'], ssl: true }, env)).to.equal(null);
      expect(reconcileKafkaLogin({ brokers: ['kafka-1:9092'] }, { brokers: ['kafka-1:9092'], ssl: false })).to.equal(null);
    });

    it('an unchanged login is not rewritten', () => {
      expect(reconcileKafkaLogin({ ...env }, env)).to.equal(null);
    });
  });

  describe('etcd-tls: the client certificate etcd requires', () => {
    const files = { ETCD_CA_FILE: '/tls/ca.crt', ETCD_CERT_FILE: '/tls/tls.crt', ETCD_KEY_FILE: '/tls/tls.key' };

    it('an https URL carries the three files; http carries none', () => {
      expect(etcdTlsFromEnv({ ETCD_URL: 'https://etcd:2379', ...files })).to.deep.equal({
        caFile: '/tls/ca.crt',
        certFile: '/tls/tls.crt',
        keyFile: '/tls/tls.key',
      });
      expect(etcdTlsFromEnv({ ETCD_URL: 'http://etcd:2379', ...files })).to.equal(undefined);
    });

    it('an https URL without a file names what is missing instead of connecting in plaintext', () => {
      expect(() => etcdTlsFromEnv({ ETCD_URL: 'HTTPS://etcd:2379', ETCD_CA_FILE: '/tls/ca.crt' })).to.throw(
        'ETCD_CERT_FILE, ETCD_KEY_FILE',
      );
    });

    it('an unreadable file fails when the client is built, where the migration check treats etcd as absent', () => {
      expect(() => etcdCredentials({ caFile: '/nonexistent/ca', certFile: '/nonexistent/c', keyFile: '/nonexistent/k' })).to.throw();
      expect(etcdCredentials(undefined)).to.equal(undefined);
    });
  });
});
