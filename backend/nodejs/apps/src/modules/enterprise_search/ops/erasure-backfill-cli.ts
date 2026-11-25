/**
 * The one-time erasure of conversations deleted before erasure existed (E7B;
 * runbook in docs/agent-harness-v2/FLAGS.md). Nothing erases
 * them unless an operator runs this:
 *
 *   node dist/modules/enterprise_search/ops/erasure-backfill-cli.js plan \
 *     --mongo-uri <uri> --db <name> [--org <orgId>] [--out <file>]
 *   node dist/modules/enterprise_search/ops/erasure-backfill-cli.js execute \
 *     --mongo-uri <uri> --db <name> --digest <sha256:…> --operator <who> \
 *     [--org <orgId>] [--wait-seconds <n>]
 *   node dist/modules/enterprise_search/ops/erasure-backfill-cli.js status \
 *     --mongo-uri <uri> --db <name> [--digest <sha256:…>]
 *
 * Under DOC_STORE=postgres the stores are PostgreSQL's (PG_NODE_URL and PG_JOBS_URL, as the application connects)
 * and `--mongo-uri` / `--db` are not taken.
 *
 * `plan` is the dry run: counts per org and store, and the digest `execute`
 * needs; it writes nothing. Prints one JSON line and exits 0, 1 on a refusal,
 * 2 on a usage error, 3 while the erasure worker is still erasing (run
 * `execute` again with the same digest to resume).
 */
import { writeFileSync } from 'node:fs';
import { hostname } from 'node:os';
import { randomUUID } from 'node:crypto';
import { mongoDb } from '../../../libs/repositories/mongo';
import {
  needsMongoOptions,
  openCliDocStore,
} from '../../agent-harness/ops/cli-doc-store';
import { createAuditChainStore } from '../../../libs/repositories/agent-harness/audit';
import {
  inDurableTransaction,
  type StoreSession,
} from '../../../libs/repositories/agent-harness/durable-transaction';
import {
  ERASURE_BACKFILL_MIGRATION,
  ERASURE_STORE_MIGRATION,
} from '../../../libs/repositories/agent-harness/migrations/mongo';
import { probeBackfillTransactions } from '../../../libs/repositories/agent-harness/erasure-backfill/mongo';
import { createErasureBackfillStore } from '../../../libs/repositories/agent-harness/erasure-backfill';
import { createErasureJobStore } from '../../../libs/repositories/agent-harness/session-erasure';
import { forOrg } from '../../../libs/context/orgContext';
import { docStore } from '../../../libs/repositories/store-select';
import { createLeaderLease } from '../../../libs/repositories/agent-harness/leader-lease';
import { ErasureBackfillService } from '../../agent-harness/application/erasure-backfill';
import {
  BackfillRefused,
  isValidOperator,
} from '../../agent-harness/domain/erasure-backfill';
import { createConversationBackfillSource } from '../../../libs/repositories/agent-harness/conversation-backfill-source';

type Options = Record<string, string>;

export interface BackfillCliBackend {
  service: Pick<ErasureBackfillService<unknown>, 'plan' | 'execute' | 'status'>;
  /** Builds what `execute` needs (indexes) and proves transactions work; refuses otherwise. */
  prepareForExecute(): Promise<void>;
  close(): Promise<void>;
}

export interface BackfillCliIo {
  out: (line: string) => void;
  err: (line: string) => void;
  writeFile: (path: string, content: string) => void;
}

const defaultIo: BackfillCliIo = {
  out: (line) => process.stdout.write(`${line}\n`),
  err: (line) => process.stderr.write(`${line}\n`),
  writeFile: (path, content) => {
    writeFileSync(path, content, { mode: 0o600 });
  },
};

const parse = (
  argv: readonly string[],
): { command: string; options: Options } => {
  const [command = '', ...rest] = argv;
  const options: Options = {};
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i] ?? '';
    if (!key.startsWith('--') || rest[i + 1] === undefined) {
      throw new Error(`bad argument ${key}`);
    }
    options[key.slice(2)] = rest[i + 1] as string;
  }
  return { command, options };
};

const required = (options: Options, ...names: string[]): void => {
  const missing = names.filter(
    (name) => options[name] === undefined || options[name] === '',
  );
  if (missing.length > 0) {
    throw new Error(`missing ${missing.map((m) => `--${m}`).join(', ')}`);
  }
};

function validate(command: string, options: Options): void {
  if (needsMongoOptions()) required(options, 'mongo-uri', 'db');
  if (command === 'execute') {
    required(options, 'digest', 'operator');
    if (!isValidOperator(options['operator'] as string)) {
      throw new Error('--operator must be 1-128 of A-Z a-z 0-9 _ . @ : + -');
    }
    const wait = options['wait-seconds'];
    if (wait !== undefined && !/^[0-9]{1,6}$/.test(wait)) {
      throw new Error('--wait-seconds must be a whole number of seconds');
    }
  } else if (command !== 'plan' && command !== 'status') {
    throw new Error('the command is plan, execute or status');
  }
}

/**
 * The production backend over the selected document store: on MongoDB the platform's models on mongoose's (already
 * open) default connection, on PostgreSQL the configured runtime.
 */
export function backfillBackend(): Omit<BackfillCliBackend, 'close'> {
  const service = new ErasureBackfillService<StoreSession>({
    source: createConversationBackfillSource(),
    store: createErasureBackfillStore(),
    jobs: createErasureJobStore(),
    audit: createAuditChainStore(),
    // A batch's unit runs as its org on PostgreSQL (row policies); MongoDB needs no org bound.
    transaction: (work, orgId) =>
      docStore() === 'postgres'
        ? forOrg(orgId, 'erasure backfill', () => inDurableTransaction(work))
        : inDurableTransaction(work),
    lease: createLeaderLease(),
    holderId: `${hostname()}:${String(process.pid)}:${randomUUID()}`,
  });
  return {
    service: service,
    async prepareForExecute() {
      // A PostgreSQL unit is always a transaction, and its tables are the migrate job's.
      if (docStore() === 'postgres') return;
      try {
        await probeBackfillTransactions();
      } catch (error) {
        throw new BackfillRefused(
          'transactions_unavailable',
          `The backfill needs a replica set (transactions): ${error instanceof Error ? error.message : String(error)}`,
        );
      }
      const db = mongoDb();
      if (db === undefined) throw new Error('No database connection');
      // Erasure jobs need their unique index even where the harness is off.
      await ERASURE_STORE_MIGRATION.up(db);
      await ERASURE_BACKFILL_MIGRATION.up(db);
    },
  };
}

export async function connectBackfill(
  uri: string | undefined,
  dbName: string | undefined,
): Promise<BackfillCliBackend> {
  const store = await openCliDocStore('erasure-backfill-cli', {
    uri,
    db: dbName,
  });
  return { ...backfillBackend(), close: () => store.close() };
}

export async function runErasureBackfillCli(
  argv: readonly string[],
  connect: (
    uri: string | undefined,
    db: string | undefined,
  ) => Promise<BackfillCliBackend> = connectBackfill,
  io: BackfillCliIo = defaultIo,
): Promise<number> {
  let command: string;
  let options: Options;
  try {
    ({ command, options } = parse(argv));
    validate(command, options);
  } catch (error) {
    io.err(error instanceof Error ? error.message : String(error));
    return 2;
  }
  const orgFilter = options['org'] ?? null;
  const backend = await connect(options['mongo-uri'], options['db']);
  try {
    if (command === 'plan') {
      const report = await backend.service.plan(orgFilter);
      const line = JSON.stringify({ command, ...report });
      if (options['out'] !== undefined)
        io.writeFile(options['out'], `${line}\n`);
      io.out(line);
      return 0;
    }
    if (command === 'status') {
      const report = await backend.service.status(options['digest'] ?? null);
      io.out(JSON.stringify({ command, ...report }));
      return 0;
    }
    await backend.prepareForExecute();
    const report = await backend.service.execute({
      digest: options['digest'] as string,
      orgFilter,
      operator: options['operator'] as string,
      ...(options['wait-seconds'] !== undefined
        ? { waitMs: Number(options['wait-seconds']) * 1000 }
        : {}),
    });
    io.out(JSON.stringify({ command, ...report }));
    return report.status === 'in_progress' ? 3 : 0;
  } catch (error) {
    if (error instanceof BackfillRefused) {
      io.out(
        JSON.stringify({
          command,
          refused: error.reasonCode,
          message: error.message,
        }),
      );
      return 1;
    }
    throw error;
  } finally {
    await backend.close();
  }
}

if (require.main === module) {
  runErasureBackfillCli(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (error: unknown) => {
      process.stderr.write(
        `${error instanceof Error ? (error.stack ?? error.message) : String(error)}\n`,
      );
      process.exit(2);
    },
  );
}
