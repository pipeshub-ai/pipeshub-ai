/**
 * ADR-KEY-01 D11 on the real KV backends (KV-1, KV-2): the configuration store's re-encryption through the
 * platform's store providers, on Redis (REDIS_IT_URL) and on etcd (ETCD_IT_URL; skipped when unset). A key with an
 * expiry keeps it; a concurrent write between the read and the compare-and-set is never overwritten.
 */
import { expect } from 'chai';
import { randomBytes } from 'node:crypto';
import { Etcd3 } from 'etcd3';
import Redis from 'ioredis';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { hashedSecret } from '../../../src/libs/encryptor/secret-key-ring';
import { KeyValueStoreFactory } from '../../../src/libs/keyValueStore/keyValueStoreFactory';
import { StoreType } from '../../../src/libs/keyValueStore/constants/KeyValueStoreType';
import type { DistributedKeyValueStore } from '../../../src/libs/keyValueStore/keyValueStore';
import { ConfigStoreReencryptStep } from '../../../src/modules/agent-harness/application/secret-key-rotation-steps';

const ALG = 'aes-256-gcm';
const oldKey = randomBytes(32).toString('hex');
const newKey = randomBytes(32).toString('hex');
const only = (raw: string) => EncryptionService.withKey(ALG, hashedSecret(raw));
const ring = EncryptionService.withRing(ALG, [hashedSecret(newKey), hashedSecret(oldKey)]);
const apply = { apply: true, checkpoint: () => undefined, progress: () => undefined };

interface Backend {
  store: DistributedKeyValueStore<string>;
  /** Writes `value` with an expiry of `ttlS`, as Python's store does. */
  putExpiring(key: string, value: string, ttlS: number): Promise<void>;
  ttl(key: string): Promise<number>;
  close(): Promise<void>;
}

function describeBackend(name: string, open: () => Promise<Backend> | null): void {
  describe(`ADR-KEY-01 config-store step on ${name}`, function () {
    this.timeout(60_000);
    let backend: Backend;
    before(async function () {
      const opened = open();
      if (opened === null) this.skip();
      backend = await opened!;
    });
    after(async () => backend?.close());

    it(`KV-1 (${name}): every value moves to the new key, an expiring one keeps its expiry, plaintext is untouched`, async () => {
      const { store } = backend;
      const p = `/kr-${randomBytes(4).toString('hex')}`;
      await store.createKey(`${p}/a`, only(oldKey).encrypt('{"a":1}'));
      await store.createKey(`${p}/plain`, '{"endpoint":"http://x"}');
      await backend.putExpiring(`${p}/lease`, JSON.stringify(only(oldKey).encrypt('"held"')), 300);
      const step = new ConfigStoreReencryptStep(
        { getAllKeys: () => store.getAllKeys(), get: <T>(k: string) => store.getKey(k) as Promise<T | null>, compareAndSet: (k, e, n) => store.compareAndSet(k, e as string | null, n as string) },
        ring,
      );
      const report = await step.run(apply);
      expect(report.moved).to.be.at.least(2);
      expect(only(newKey).decrypt(await store.getKey(`${p}/a`))).to.equal('{"a":1}');
      expect(only(newKey).decrypt(JSON.parse((await store.getKey(`${p}/lease`)) as string) as string)).to.equal('"held"');
      expect(await store.getKey(`${p}/plain`)).to.equal('{"endpoint":"http://x"}');
      const ttl = await backend.ttl(`${p}/lease`);
      expect(ttl).to.be.greaterThan(0);
      expect(ttl).to.be.at.most(300);
    });

    it(`KV-2 (${name}): a write made after the read wins over the re-encryption`, async () => {
      const { store } = backend;
      const key = `/kr-${randomBytes(4).toString('hex')}/edited`;
      await store.createKey(key, only(oldKey).encrypt('before'));
      let once = true;
      const step = new ConfigStoreReencryptStep(
        {
          getAllKeys: async () => [key],
          get: <T>(k: string) => store.getKey(k) as Promise<T | null>,
          compareAndSet: async (k, e, n) => {
            if (once) {
              once = false;
              await store.updateValue(k, only(newKey).encrypt('edited'));
            }
            return store.compareAndSet(k, e as string | null, n as string);
          },
        },
        ring,
      );
      await step.run(apply);
      expect(only(newKey).decrypt(await store.getKey(key))).to.equal('edited');
    });
  });
}

describeBackend('Redis', () => {
  const raw = process.env.REDIS_IT_URL;
  if (!raw) throw new Error('REDIS_IT_URL must be set for the KV rotation itest');
  const url = new URL(raw);
  const keyPrefix = `pxh_keyring_kv:${randomBytes(4).toString('hex')}:`;
  const config = { host: url.hostname, port: Number(url.port || '6379'), keyPrefix, db: 0 };
  const store = KeyValueStoreFactory.createStore<string>(StoreType.Redis, config, (v) => Buffer.from(v), (b) => b.toString());
  const client = new Redis({ host: config.host, port: config.port });
  return Promise.resolve({
    store,
    putExpiring: async (key, value, ttlS) => {
      await client.set(`${keyPrefix}${key}`, value, 'EX', ttlS);
    },
    ttl: (key) => client.ttl(`${keyPrefix}${key}`),
    close: async () => {
      const keys = await client.keys(`${keyPrefix}*`);
      if (keys.length > 0) await client.del(...keys);
      client.disconnect();
      await (store as unknown as { disconnect?: () => Promise<void> }).disconnect?.();
    },
  });
});

describeBackend('etcd', () => {
  const raw = process.env.ETCD_IT_URL;
  if (!raw) return null;
  const url = new URL(raw);
  const store = KeyValueStoreFactory.createStore<string>(
    StoreType.Etcd3,
    { host: `${url.protocol}//${url.hostname}`, port: Number(url.port || '2379'), dialTimeout: 2000 },
    (v) => Buffer.from(v),
    (b) => b.toString(),
  );
  const client = new Etcd3({ hosts: [`${url.protocol}//${url.host}`] });
  return Promise.resolve({
    store,
    putExpiring: async (key, value, ttlS) => {
      const lease = client.lease(ttlS, { autoKeepAlive: false });
      await lease.put(key).value(value);
    },
    ttl: async (key) => {
      const leaseId = (await client.get(key).exec()).kvs[0]?.lease;
      if (!leaseId || leaseId === '0') return -1;
      const live = await client.leaseClient.leaseTimeToLive({ ID: leaseId });
      return Number(live.TTL);
    },
    close: async () => {
      await client.delete().prefix('/kr-').exec();
      client.close();
    },
  });
});
