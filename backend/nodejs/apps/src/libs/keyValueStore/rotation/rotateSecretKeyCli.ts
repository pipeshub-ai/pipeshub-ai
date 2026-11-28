import { config } from 'dotenv';
import { applyStoreUmbrella } from '../../config/storeUmbrella';
import { loadConfigurationManagerConfig } from '../../../modules/configuration_manager/config/config';
import { KeyValueStoreFactory } from '../keyValueStoreFactory';
import { KeyValueStoreType, StoreType } from '../constants/KeyValueStoreType';
import {
  RedisConnectionProviderFactory,
  getPreparedRedisProvider,
} from '../../services/redis/connectionProviderFactory';
import { rotateEncryptedValues, RotationResult } from './rotateSecretKey';

type RotationStore = Parameters<typeof rotateEncryptedValues>[0] & {
  disconnect?: () => Promise<void>;
};

/** Same store the API builds, in the order index.ts and app.ts set it up. */
export async function createStore(): Promise<RotationStore> {
  config({ quiet: true });
  applyStoreUmbrella();
  const cfg = loadConfigurationManagerConfig();
  const storeType = KeyValueStoreType.fromString(cfg.storeType);
  if (storeType === StoreType.Redis) {
    await RedisConnectionProviderFactory.ensureProviderModuleLoaded();
    await getPreparedRedisProvider();
  }
  return KeyValueStoreFactory.createStore<string>(
    storeType,
    storeType === StoreType.Redis ? cfg.redisConfig : cfg.storeConfig,
    (v: string) => Buffer.from(v),
    (b: Buffer) => b.toString(),
  );
}

interface CliDeps {
  env: NodeJS.ProcessEnv;
  argv: string[];
  createStore: () => Promise<RotationStore>;
  log: (line: string) => void;
  error: (line: string) => void;
}

/**
 * Secrets come from the environment only (argv is visible in process listings).
 * Dry run unless --apply is passed. Prints counts and key names, never values.
 * Exit codes: 0 done, 1 aborted/incomplete/nothing found/error, 2 missing input.
 */
export async function runCli(deps: CliDeps): Promise<number> {
  const { env, argv, log, error } = deps;
  const oldSecret = env.OLD_SECRET_KEY;
  const newSecret = env.NEW_SECRET_KEY;
  if (!oldSecret || !newSecret) {
    error('Set OLD_SECRET_KEY and NEW_SECRET_KEY in the environment.');
    return 2;
  }
  const dryRun = !argv.includes('--apply');
  const skipUnreadable = argv.includes('--skip-unreadable');
  // Only so loadConfigurationManagerConfig does not throw; never used to encrypt.
  env.SECRET_KEY ||= oldSecret;

  let store: RotationStore;
  try {
    store = await deps.createStore();
  } catch (e) {
    error(`Could not connect to the key-value store: ${(e as Error).message}`);
    return 1;
  }
  try {
    const r = await rotateEncryptedValues(store, oldSecret, newSecret, {
      dryRun,
      skipUnreadable,
    });
    return report(r, dryRun, skipUnreadable, log, error);
  } catch (e) {
    error(`Rotation failed before any write: ${(e as Error).message}`);
    return 1;
  } finally {
    await store.disconnect?.();
  }
}

function report(
  r: RotationResult,
  dryRun: boolean,
  skipUnreadable: boolean,
  log: CliDeps['log'],
  error: CliDeps['error'],
): number {
  const total =
    r.rotated +
    r.alreadyRotated +
    r.plaintext +
    r.failed.length +
    r.conflicts.length +
    r.writeErrors.length +
    r.skipped.length;
  log(
    `rotated=${r.rotated} alreadyRotated=${r.alreadyRotated} plaintext=${r.plaintext} failed=${r.failed.length} ` +
      `conflicts=${r.conflicts.length} writeErrors=${r.writeErrors.length} skipped=${r.skipped.length}`,
  );
  for (const [label, keys] of [
    ['failed', r.failed],
    ['conflicts', r.conflicts],
    ['writeErrors', r.writeErrors],
    ['skipped', r.skipped],
  ] as const)
    keys.forEach((k) => {
      log(`${label}: ${k}`);
    });

  let code = 0;
  if (total === 0) {
    error(
      'No keys found. Check the store settings (KV_STORE_TYPE, REDIS_*, REDIS_KV_PREFIX); nothing was rotated.',
    );
    return 1;
  }
  if (r.failed.length && !skipUnreadable) {
    error(
      'ABORTED, nothing written: the failed keys hold values that decrypt with neither secret. ' +
        'Check them; if none is a live secret under another key, re-run with --skip-unreadable to leave them as they are.',
    );
    return 1;
  }
  if (r.failed.length)
    error(
      'The failed keys were left as they are (--skip-unreadable), including any readable ' +
        'encrypted field in the same value: those stay on the old key.',
    );
  if (r.conflicts.length || r.writeErrors.length) {
    error(
      'INCOMPLETE: the conflict keys were not rewritten (the value changed during the run, or the store did not apply the write) ' +
        'and the writeErrors keys hit a store error. Re-run to finish.',
    );
    code = 1;
  }
  if (code === 0) log(dryRun ? 'DRY RUN (nothing written)' : 'APPLIED');
  log(
    'Reminder: data outside the key-value store is not rotated (OAuth app client secrets in the database, encrypted connector sync fields). See docs/secret-key-rotation.md.',
  );
  return code;
}

if (require.main === module) {
  runCli({
    env: process.env,
    argv: process.argv,
    createStore,
    log: (l) => {
      console.log(l);
    },
    error: (l) => {
      console.error(l);
    },
  }).then(
    (code) => process.exit(code),
    (e: unknown) => {
      console.error(
        `Rotation failed: ${e instanceof Error ? e.message : 'unknown error'}`,
      );
      process.exit(1);
    },
  );
}
