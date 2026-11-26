/**
 * CPD-7's D2 migration of today's toolset instances, MCP instances and
 * manifests and agent edges into the control plane's installations and grants:
 *
 *   node dist/modules/agent-harness/ops/capability-migration-cli.js dry-run|apply|revert \
 *     --mongo-uri <uri> --db <name> --ai-backend <query service URL> \
 *     --secrets-file <JSON {jwtSecret, scopedJwtSecret}> [--org <orgId>]
 *
 * `dry-run` reports per org what `apply` would write and writes nothing;
 * `apply` writes each object in its own audited control-plane transaction with
 * its ledger entry; a second `apply` writes nothing; `revert` removes what the
 * ledger lists and leaves (as `drifted`) what changed since. Without `--org`
 * every org runs. An `apply` that neither failed nor held anything back
 * records the org as migrated (`migrated: true` in its line; also an org with
 * nothing to migrate): with `CONTROL_PLANE_PIN=on` the org's effective set is
 * from then on the only authority for what a run may use, so an item without
 * a grant or a credential binding is no longer offered. `revert` takes the
 * record back first. Prints one JSON line per org; exits 0, 1 when an org failed
 * or held anything back, 2 on a usage error. The secrets come from a file so
 * they never appear in the process list. An installation that `revert` removes
 * has its credential bindings revoked first, in the deployment's vault: the
 * KV store and SECRET_KEY are read from the same environment as the Node
 * service (KV_STORE_TYPE and its settings). Under DOC_STORE=postgres the stores are PostgreSQL's (PG_NODE_URL and
 * PG_JOBS_URL, as the application connects) and `--mongo-uri` / `--db` are not taken.
 */
import { readFileSync } from 'node:fs';
import { needsMongoOptions, openCliDocStore } from './cli-doc-store';
import {
  CapabilityMigration,
  CONTROL_PLANE_MIGRATIONS,
  createInstallationStore,
  createGrantStore,
  createCapabilityLedger,
  createControlPlaneWriteTransaction,
  createBindingService,
  HttpLegacyCapabilitySource,
  MongoControlPlaneTransaction,
  releaseItemsOf,
  silentControlPlaneLog,
  type MigrationMode,
  type OrgReport,
} from '../../control-plane';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { canonicalDigest } from '../../../libs/utils/canonical-json';
import { loadConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { registerAppUpstreams } from '../../tokens_manager/config/config';
import { configurationCipher } from '../adapters/configuration/encrypted-config-document-store';
import { activeOrgIds } from '../../../libs/repositories/agent-harness/active-orgs';
import { ChainConfigAuditLog } from '../adapters/chain-config-audit-log';
import { DURABLE_TRANSACTION_OPTIONS } from '../../../libs/repositories/agent-harness/durable-transaction/mongo';
import { createAuditChainStore } from '../../../libs/repositories/agent-harness/audit';

const MODES: Readonly<Record<string, MigrationMode>> = {
  'dry-run': 'dry-run',
  apply: 'apply',
  revert: 'revert',
};

export interface CapabilityMigrationCliBackend {
  readonly migration: Pick<CapabilityMigration, 'run' | 'runFor'>;
  close(): Promise<void>;
}

type Options = Record<string, string>;

function parse(argv: readonly string[]): {
  mode: MigrationMode;
  options: Options;
} {
  const [command = '', ...rest] = argv;
  const mode = MODES[command];
  if (mode === undefined)
    throw new Error('the command is dry-run, apply or revert');
  const options: Options = {};
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i] ?? '';
    const value = rest[i + 1];
    if (!key.startsWith('--') || value === undefined)
      throw new Error(`bad argument ${key}`);
    options[key.slice(2)] = value;
  }
  const missing = [
    ...(needsMongoOptions() ? ['mongo-uri', 'db'] : []),
    'ai-backend',
    'secrets-file',
  ].filter((name) => !options[name]);
  if (missing.length > 0)
    throw new Error(`missing ${missing.map((m) => `--${m}`).join(', ')}`);
  return { mode, options };
}

function readSecrets(path: string): {
  jwtSecret: string;
  scopedJwtSecret: string;
} {
  const raw = JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>;
  const { jwtSecret, scopedJwtSecret } = raw;
  if (typeof jwtSecret !== 'string' || typeof scopedJwtSecret !== 'string') {
    throw new Error('the secrets file holds {jwtSecret, scopedJwtSecret}');
  }
  return { jwtSecret, scopedJwtSecret };
}

/**
 * The production backend: the document store's stores, ledger and write transaction (the replica-set transaction
 * on MongoDB), the query service's driver.
 */
export async function connectCapabilityMigration(
  options: Options,
): Promise<CapabilityMigrationCliBackend> {
  const secrets = readSecrets(options['secrets-file'] as string);
  // This process never loads the app's config, which is where the services calls may go to are registered (#6192).
  registerAppUpstreams({ aiBackend: options['ai-backend'] });
  const store = await openCliDocStore('capability-migration-cli', {
    uri: options['mongo-uri'],
    db: options['db'],
  });
  // PostgreSQL's tables are the migrate job's; Mongo's indexes are built here.
  if (store.db !== null) {
    for (const migration of CONTROL_PLANE_MIGRATIONS) {
      await migration.up(store.db);
    }
  }
  const kv = KeyValueStoreService.getInstance(loadConfigurationManagerConfig());
  await kv.connect();
  const audit = new ChainConfigAuditLog(createAuditChainStore());
  const transaction = createControlPlaneWriteTransaction({
    audit,
    mongo: () =>
      new MongoControlPlaneTransaction({
        audit,
        transaction: DURABLE_TRANSACTION_OPTIONS,
      }),
  });
  const migration = new CapabilityMigration({
    transaction,
    installations: createInstallationStore(),
    grants: createGrantStore(),
    ledger: createCapabilityLedger(),
    source: new HttpLegacyCapabilitySource(() => ({
      aiBackend: options['ai-backend'] as string,
      ...secrets,
    })),
    release: releaseItemsOf,
    digest: canonicalDigest,
    // A removed installation's bindings are revoked in the deployment's credential vault.
    credentials: createBindingService({
      transaction,
      kv,
      cipher: configurationCipher,
    }),
    orgs: activeOrgIds,
    log: silentControlPlaneLog,
  });
  return {
    migration,
    close: async () => {
      await store.close();
      await kv.disconnect();
    },
  };
}

const failed = (report: OrgReport): boolean =>
  report.failed !== null || report.holds.length > 0;

export async function runCapabilityMigrationCli(
  argv: readonly string[],
  connect: (
    options: Options,
  ) => Promise<CapabilityMigrationCliBackend> = connectCapabilityMigration,
  out: (line: string) => void = (line) => process.stdout.write(`${line}\n`),
  err: (line: string) => void = (line) => process.stderr.write(`${line}\n`),
): Promise<number> {
  let parsed: { mode: MigrationMode; options: Options };
  try {
    parsed = parse(argv);
  } catch (error) {
    err(error instanceof Error ? error.message : String(error));
    return 2;
  }
  const backend = await connect(parsed.options);
  try {
    const org = parsed.options['org'];
    const reports =
      org === undefined
        ? await backend.migration.run(parsed.mode)
        : await backend.migration.runFor(parsed.mode, [org]);
    for (const report of reports) out(JSON.stringify(report));
    return reports.some(failed) ? 1 : 0;
  } finally {
    await backend.close();
  }
}

if (require.main === module) {
  runCapabilityMigrationCli(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (error: unknown) => {
      process.stderr.write(
        `${error instanceof Error ? error.message : String(error)}\n`,
      );
      process.exit(2);
    },
  );
}
