/**
 * Drives Node's binder against a real Redis and MongoDB, for the Python
 * cross-language test (CPD-8 XL-B, backend/python/tests/unit/harness/
 * credentials/test_binding_interop.py): the production KV provider and
 * cipher (SECRET_KEY from the environment), the Mongo binding store.
 *
 *   REDIS_HOST=… REDIS_PORT=… REDIS_KV_PREFIX=test: SECRET_KEY=… MONGO_IT_URI=… BINDINGS_DB=… \
 *   TS_NODE_PROJECT=tsconfig.test.json npx ts-node --transpile-only \
 *     tests/modules/control-plane/support/binding-vault-cli.ts bind <org> <installation> <owner> <secret>
 *
 * Commands: `bind <org> <installation> <owner> <secret>`, `refresh <org> <binding> <revision> <secret>`,
 * `revoke <org> <binding> <revision>`. Prints one JSON line (never the secret).
 */
import 'reflect-metadata';
import mongoose from 'mongoose';
import { RedisDistributedKeyValueStore } from '../../../../src/libs/keyValueStore/providers/RedisDistributedKeyValueStore';
import { closeAllRedisProviders } from '../../../../src/libs/services/redis/connectionProviderFactory';
import { ulid } from '../../../../src/libs/utils/ulid';
import { configurationCipher } from '../../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';
import { KvBindingVault, KvOAuthStateStore } from '../../../../src/modules/control-plane/adapters/kv/kv-binding-vault';
import { BINDING_MIGRATION } from '../../../../src/libs/repositories/control-plane/migrations/mongo';
import { MongoBindingStore } from '../../../../src/libs/repositories/control-plane/bindings/mongo';
import { VaultCredentialBinder } from '../../../../src/modules/control-plane/application/vault-credential-binder';
import { anInstallation } from './cp-objects';
import { testControlPlane } from './in-memory';

async function main(): Promise<unknown> {
  const redis = new RedisDistributedKeyValueStore<string>(
    {
      host: process.env.REDIS_HOST ?? '127.0.0.1',
      port: Number(process.env.REDIS_PORT ?? '6379'),
      keyPrefix: process.env.REDIS_KV_PREFIX ?? 'pipeshub:kv:',
    },
    (value: string) => Buffer.from(value),
    (buffer: Buffer) => buffer.toString(),
  );
  const store = {
    get: <T>(key: string) => redis.getKey(key) as Promise<T | null>,
    compareAndSet: <T>(key: string, expected: T | null, value: T) =>
      redis.compareAndSet(key, expected as string | null, value as string),
    delete: (key: string) => redis.deleteKey(key),
    listKeysInDirectory: (directory: string) => redis.listKeysInDirectory(directory),
  };
  await mongoose.connect(String(process.env.MONGO_IT_URI), { dbName: String(process.env.BINDINGS_DB) });
  await BINDING_MIGRATION.up(mongoose.connection.db!);
  const bindings = new MongoBindingStore();
  const binder = new VaultCredentialBinder({
    transaction: testControlPlane(),
    bindings,
    vault: new KvBindingVault(store, configurationCipher()),
    states: new KvOAuthStateStore(store),
    oauthStarts: [],
    newId: () => ulid(),
    now: () => Date.now(),
  });
  const [command, orgId, a, b, c] = process.argv.slice(2) as [string, string, string, string, string | undefined];
  const ctx = { orgId, actor: 'service:cpd8-interop' };
  const material = (secret: string) => ({ kind: 'api_key' as const, value: { apiKey: secret } });
  const answer = (binding: { id: string; revision: number; status: string; secret: { versionDigest: string } | null }) => ({
    id: binding.id,
    revision: binding.revision,
    status: binding.status,
    digest: binding.secret?.versionDigest ?? null,
  });
  switch (command) {
    case 'bind': {
      const start = await binder.begin(ctx, anInstallation({ id: a, orgId }), b as 'org', 'api_key', material(String(c)));
      return answer(start.binding);
    }
    case 'refresh':
      return answer(await binder.refreshed(ctx, a, Number(b), material(String(c))));
    case 'revoke':
      return answer(await binder.revoke(ctx, a, Number(b)));
    case 'drop':
      await mongoose.connection.dropDatabase();
      return { dropped: true };
    default:
      throw new Error(`unknown command ${command}`);
  }
}

main()
  .then(async (result) => {
    process.stdout.write(`${JSON.stringify(result)}\n`);
    await closeAllRedisProviders();
    await mongoose.disconnect();
    process.exit(0);
  })
  .catch(async (error: unknown) => {
    process.stderr.write(`${error instanceof Error ? `${error.name}: ${error.message}` : 'Error'}\n`);
    await closeAllRedisProviders();
    await mongoose.disconnect();
    process.exit(1);
  });
