/**
 * The Node side of backend/python/tests/integration/config/test_kv_cross_language.py: runs key-value operations through the real
 * KeyValueStoreService (KV_STORE_TYPE and the store's settings from the environment) as Node callers do (they encrypt with the
 * platform cipher before `set` and decrypt after `get`) and prints one line `KV_RESULT <json>`.
 *   ops: [{"op": "get" | "rawget", "key": k} | {"op": "set", "key": k, "text": t} | {"op": "cas", "key": k, "expected": raw, "text": t}]
 * `rawget` reads the store below the service, as builds before the shared reading of stored forms did.
 */
import 'reflect-metadata';
import { EncryptionService } from '../../src/libs/encryptor/encryptor';
import { KeyValueStoreService } from '../../src/libs/services/keyValueStore.service';
import { loadConfigurationManagerConfig } from '../../src/modules/configuration_manager/config/config';

interface Op {
  op: 'get' | 'rawget' | 'set' | 'cas';
  key: string;
  text?: string;
  expected?: string | null;
}

async function main(): Promise<void> {
  const ops = JSON.parse(process.argv[2] ?? '[]') as Op[];
  const config = loadConfigurationManagerConfig();
  const platform = EncryptionService.getInstance(config.algorithm, config.secretKey);
  const kv = KeyValueStoreService.getInstance(config);
  await kv.connect();
  const below = (kv as unknown as { store: { getKey(k: string): Promise<unknown> } }).store;
  const out: unknown[] = [];
  for (const o of ops) {
    try {
      if (o.op === 'get' || o.op === 'rawget') {
        const raw = (o.op === 'get' ? await kv.get<string>(o.key) : await below.getKey(o.key)) as string | null;
        out.push({ raw, text: raw === null ? null : platform.decrypt(raw) });
      } else if (o.op === 'set') {
        await kv.set<string>(o.key, platform.encrypt(o.text ?? ''));
        out.push({ ok: true });
      } else {
        out.push({ ok: await kv.compareAndSet<string>(o.key, o.expected ?? null, platform.encrypt(o.text ?? '')) });
      }
    } catch (e) {
      out.push({ error: (e as Error).constructor.name });
    }
  }
  process.stdout.write(`KV_RESULT ${JSON.stringify(out)}\n`);
  await kv.disconnect();
  process.exit(0);
}

void main();
