/**
 * ADR-KEY-01 through the operator's entry point with its production backend (KI-1..KI-3): org data keys and OAuth
 * apps on the replica set, configuration values in the deployment's KV store (Redis, a key with an expiry among
 * them), the lease in Mongo. Written under one generated key, rotated to another, retired. The egress step is a stub
 * here (its own tests are Python's). Needs MONGO_IT_URI (rs0) and REDIS_IT_URL.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomBytes } from 'node:crypto';
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Redis from 'ioredis';
import mongoose, { Types } from 'mongoose';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { hashedSecret } from '../../../src/libs/encryptor/secret-key-ring';
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service';
import { MongoDataKeyStore } from '../../../src/libs/repositories/agent-harness/data-protection/mongo';
import { MongoLeaderLease } from '../../../src/libs/repositories/agent-harness/leader-lease/mongo';
import { MongoOAuthAppRepository } from '../../../src/libs/repositories/oauth-app/mongo';
import { OAuthGrantType } from '../../../src/libs/repositories/oauth-app';
import { HkdfKeyEncryptionKey, platformKeyEncryptionKey } from '../../../src/modules/agent-harness/adapters/crypto/hkdf-key-encryption-key';
import { OrgDataKeys } from '../../../src/modules/agent-harness/application/org-data-keys';
import { ROTATION_LEASE } from '../../../src/modules/agent-harness/application/secret-key-rotation';
import { runRotateSecretKeyCli } from '../../../src/modules/agent-harness/ops/rotate-secret-key-cli';
import { loadConfigurationManagerConfig } from '../../../src/modules/configuration_manager/config/config';
import { Org } from '../../../src/modules/user_management/schema/org.schema';
import { harnessMongoUri } from './support/harness-mongo';

const DB = `harness_keyring_it_${Date.now()}_${process.pid}`;
const ENV = ['KV_STORE_TYPE', 'REDIS_HOST', 'REDIS_PORT', 'REDIS_KV_PREFIX', 'SECRET_KEY', 'SECRET_KEY_PREVIOUS', 'SECRET_KEY_NEXT', 'SECRET_KEY_KDF', 'DOC_STORE'];
const ALG = 'aes-256-gcm';

type Singletons = { instance?: unknown };
const encryptionSingleton = EncryptionService as unknown as Singletons;
const kvSingleton = KeyValueStoreService as unknown as Singletons;

describe('ADR-KEY-01: rotate-secret-key on the replica set and Redis', function () {
  this.timeout(120_000);
  const saved: Record<string, string | undefined> = {};
  const savedSingletons = { encryption: encryptionSingleton.instance, kv: kvSingleton.instance };
  const oldKey = randomBytes(32).toString('hex');
  const newKey = randomBytes(32).toString('hex');
  const orgs = [new Types.ObjectId().toHexString(), new Types.ObjectId().toHexString()];
  let dir: string;
  let python: string;
  let redis: Redis;
  let prefix: string;

  const fresh = (): void => {
    encryptionSingleton.instance = undefined;
    kvSingleton.instance = undefined;
  };
  const kv = () => KeyValueStoreService.getInstance(loadConfigurationManagerConfig());
  const only = (raw: string) => EncryptionService.withKey(ALG, hashedSecret(raw));

  async function connected<T>(work: () => Promise<T>): Promise<T> {
    await mongoose.connect(harnessMongoUri(), { dbName: DB, autoIndex: true });
    await kv().connect();
    try {
      return await work();
    } finally {
      await kv().disconnect();
      await mongoose.disconnect();
    }
  }

  const cli = async (...argv: string[]) => {
    const out: string[] = [];
    const err: string[] = [];
    fresh();
    const code = await runRotateSecretKeyCli(
      [...argv, '--mongo-uri', harnessMongoUri(), '--db', DB, '--python', python],
      undefined,
      (line) => out.push(line),
      (line) => err.push(line),
    );
    const result = JSON.parse(out.at(-1) ?? '{}') as { ok?: boolean; steps?: Array<Record<string, number | string>>; refused?: string };
    return { code, out, err, result };
  };

  before(async () => {
    const raw = process.env.REDIS_IT_URL;
    if (!raw) throw new Error('REDIS_IT_URL must be set for the key rotation itest');
    const url = new URL(raw);
    for (const name of ENV) saved[name] = process.env[name];
    prefix = `pxh_keyring:${randomBytes(6).toString('hex')}:kv:`;
    process.env.KV_STORE_TYPE = 'redis';
    process.env.REDIS_HOST = url.hostname;
    process.env.REDIS_PORT = url.port || '6379';
    process.env.REDIS_KV_PREFIX = prefix;
    delete process.env.DOC_STORE;
    // The values are sealed and read here with the SHA-256 key (only()), whatever profile the runner has.
    process.env.SECRET_KEY_KDF = 'sha256';
    delete process.env.SECRET_KEY_PREVIOUS;
    delete process.env.SECRET_KEY_NEXT;
    process.env.SECRET_KEY = oldKey;
    redis = new Redis({ host: url.hostname, port: Number(url.port || '6379') });
    dir = mkdtempSync(join(tmpdir(), 'keyring-it-'));
    python = join(dir, 'python');
    writeFileSync(
      python,
      '#!/bin/sh\necho \'{"step":"egress-state","total":0,"current":0,"previous":0,"moved":0,"unreadable":0,"draining":0}\'\n',
    );
    chmodSync(python, 0o700);

    // The deployment before the rotation: everything written under the old key.
    fresh();
    await connected(async () => {
      for (const orgId of orgs) {
        await Org.collection.insertOne({ _id: new Types.ObjectId(orgId), slug: `org-${orgId}`, isDeleted: false });
        const keys = new OrgDataKeys({ store: new MongoDataKeyStore(), kek: platformKeyEncryptionKey() });
        await keys.active(orgId);
        await keys.rotate(orgId);
        await new MongoOAuthAppRepository().create({
          clientId: `cid-${orgId}`,
          clientSecretEncrypted: only(oldKey).encrypt(`client-secret-${orgId}`),
          name: 'App',
          orgId,
          createdBy: new Types.ObjectId().toHexString(),
          redirectUris: ['https://a.example/cb'],
          allowedGrantTypes: [OAuthGrantType.CLIENT_CREDENTIALS],
          allowedScopes: ['org:read'],
          isConfidential: true,
        });
      }
      await kv().set('/services/keyring/a', only(oldKey).encrypt('{"token":"a"}'));
      await kv().set('/services/keyring/plain', '{"endpoint":"http://x"}');
    });
    // A Python-written value with an expiry (a lease): JSON-quoted ciphertext under the old key.
    await redis.set(`${prefix}/services/keyring/lease`, JSON.stringify(only(oldKey).encrypt('"held"')), 'EX', 600);

    // The switch: the new key writes, the old one is still read.
    process.env.SECRET_KEY = newKey;
    process.env.SECRET_KEY_PREVIOUS = oldKey;
  });

  after(async () => {
    fresh();
    await connected(async () => {
      await mongoose.connection.dropDatabase();
    });
    const keys = await redis.keys(`${prefix}*`);
    if (keys.length > 0) await redis.del(...keys);
    redis.disconnect();
    rmSync(dir, { recursive: true, force: true });
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    encryptionSingleton.instance = savedSingletons.encryption;
    kvSingleton.instance = savedSingletons.kv;
  });

  it('KI-1: dry-run counts what is under the old key; a held lock refuses rotate; retire is refused first', async () => {
    const dry = await cli('dry-run');
    expect(dry.code, dry.err.join('\n')).to.equal(0);
    const previous = Object.fromEntries((dry.result.steps ?? []).map((s) => [s['step'], s['previous']]));
    expect(previous).to.deep.equal({ 'data-keys': 4, 'egress-state': 0, 'sync-points': 0, 'config-store': 2, 'oauth-client-secrets': 2 });
    expect((await cli('retire')).code).to.equal(1);

    // The CLI closes the connection it opened, so the lease is taken and released around it.
    const lease = async (work: (l: MongoLeaderLease) => Promise<unknown>) => {
      await mongoose.connect(harnessMongoUri(), { dbName: DB });
      try {
        await work(new MongoLeaderLease());
      } finally {
        await mongoose.disconnect();
      }
    };
    await lease(async (l) => expect(await l.tryAcquire(ROTATION_LEASE, 'someone-else', 60_000)).to.equal(true));
    const held = await cli('rotate');
    expect(held.code).to.equal(1);
    expect(held.err.join('\n')).to.match(/another run of the rotation holds the lock/);
    await lease((l) => l.release(ROTATION_LEASE, 'someone-else'));
  });

  it('KI-2: rotate moves every item to the new key, keeps the lease\'s expiry, and verify and retire pass', async () => {
    const run = await cli('rotate');
    expect(run.code, run.err.join('\n')).to.equal(0);
    const moved = (run.result.steps ?? []).reduce((sum, s) => sum + Number(s['moved']), 0);
    expect(moved).to.equal(8);
    expect([...run.out, ...run.err].join('\n')).not.to.include(oldKey).and.not.to.include(newKey);

    fresh();
    await connected(async () => {
      const kekNew = new HkdfKeyEncryptionKey(() => Buffer.from(hashedSecret(newKey), 'hex'));
      for (const orgId of orgs) {
        for (const key of await new MongoDataKeyStore().list(orgId)) {
          expect(key.kekId).to.equal(kekNew.id);
          await kekNew.unwrap(key.wrapped, { orgId, kid: key.kid, version: key.version });
        }
        const app = await new MongoOAuthAppRepository().findByClientId(`cid-${orgId}`);
        expect(only(newKey).decrypt(app?.clientSecretEncrypted)).to.equal(`client-secret-${orgId}`);
      }
      expect(only(newKey).decrypt(await kv().get<string>('/services/keyring/a'))).to.equal('{"token":"a"}');
      expect(await kv().get<string>('/services/keyring/plain')).to.equal('{"endpoint":"http://x"}');
    });
    const lease = await redis.get(`${prefix}/services/keyring/lease`);
    expect(only(newKey).decrypt(JSON.parse(lease as string) as string)).to.equal('"held"');
    expect(await redis.ttl(`${prefix}/services/keyring/lease`)).to.be.within(1, 600);

    expect((await cli('verify')).code).to.equal(0);
    const retire = await cli('retire');
    expect(retire.code).to.equal(0);
    expect(retire.out.at(-1)).to.include('remove SECRET_KEY_PREVIOUS');
  });

  it('KI-3: with SECRET_KEY_PREVIOUS removed, the new key alone reads everything and a second rotate is refused', async () => {
    delete process.env.SECRET_KEY_PREVIOUS;
    expect((await cli('verify')).code).to.equal(0);
    const again = await cli('rotate');
    expect(again.code).to.equal(1);
    expect(again.err.join('\n')).to.match(/SECRET_KEY_PREVIOUS is not set/);
    process.env.SECRET_KEY_PREVIOUS = oldKey;
  });
});
