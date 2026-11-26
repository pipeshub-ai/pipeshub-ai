/**
 * Drives Node's model-account vault against a real Redis, for the Python
 * cross-language test (P2.4b XL, backend/python/tests/unit/harness/credentials/
 * test_node_vault_interop.py). The store is the production Redis provider
 * with KeyValueStoreService's serializer, the cipher the production one
 * (SECRET_KEY from the environment).
 *
 *   REDIS_HOST=127.0.0.1 REDIS_PORT=6379 REDIS_KV_PREFIX=test: SECRET_KEY=... \
 *   TS_NODE_PROJECT=tsconfig.test.json npx ts-node --transpile-only \
 *     tests/modules/agent-harness/support/model-account-vault-cli.ts put <org> <user> <provider> <apiKey>
 *
 * Commands: `put <org> <user> <provider> <apiKey>`, `revoke <org> <user> <provider>`,
 * `list <org> <user>`. Prints one JSON line.
 */
import 'reflect-metadata';
import { RedisDistributedKeyValueStore } from '../../../../src/libs/keyValueStore/providers/RedisDistributedKeyValueStore';
import { closeAllRedisProviders } from '../../../../src/libs/services/redis/connectionProviderFactory';
import { createModelAccountVault } from '../../../../src/modules/agent-harness/adapters/configuration/model-account-vault';

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
  const vault = createModelAccountVault(store);
  const [command, orgId, userId, provider, apiKey] = process.argv.slice(2) as [
    string,
    string,
    string,
    string | undefined,
    string | undefined,
  ];
  const owner = { kind: 'user', userId } as const;
  switch (command) {
    case 'put':
      return vault.put(
        orgId,
        owner,
        String(provider),
        {
          kind: 'user_api_key',
          provider: String(provider),
          label: null,
          material: { provider: String(provider), apiKey: String(apiKey) },
          hint: String(apiKey),
        },
        userId,
      );
    case 'revoke':
      if (!(await vault.revoke(orgId, owner, String(provider), userId))) {
        throw new Error('not found');
      }
      return { revoked: true };
    case 'list':
      return { accounts: await vault.list(orgId, owner) };
    default:
      throw new Error(`unknown command ${command}`);
  }
}

main()
  .then(async (result) => {
    process.stdout.write(`${JSON.stringify(result)}\n`);
    await closeAllRedisProviders();
    process.exit(0);
  })
  .catch(async (error: unknown) => {
    process.stderr.write(`${error instanceof Error ? error.name : 'Error'}\n`);
    await closeAllRedisProviders();
    process.exit(1);
  });
