/**
 * Session backup and restore from the command line (P9; runbook in
 * docs/agent-harness-v2/guides/runbooks.md):
 *
 *   node dist/modules/agent-harness/ops/session-backup-cli.js export \
 *     --mongo-uri <uri> --db <name> --org <orgId> --session <sessionId> --out <dir>
 *   node dist/modules/agent-harness/ops/session-backup-cli.js restore \
 *     --mongo-uri <uri> --db <name> --in <dir>
 *
 * Under DOC_STORE=postgres the store is PostgreSQL (PG_NODE_URL, as the application connects) and `--mongo-uri` /
 * `--db` are not taken; the archive is then a `pipeshub.session-backup.postgres/1` one, restorable only there.
 *
 * Prints one JSON line (the manifest's counts, or the refusal's reason code)
 * and exits 0, or 1 on a refusal, or 2 on a usage error. The archive holds the
 * session's documents as Mongo stores them and the org's wrapped data keys,
 * never a plaintext key: the journal and the workspace key stay sealed, and
 * the target deployment unseals them only with the same KEK. The
 * conversation's messages, citations and run tombstones are stored, and so
 * archived, in plaintext: the directory is made 0700 and every file 0600, and
 * the operator keeps the archive as they would a database dump.
 */
import { openMongoConnection } from '../../../libs/repositories/mongo';
import { PgSessionBackup } from '../../../libs/repositories/agent-harness/session-backup/pg';
import { MongoSessionBackup } from '../../../libs/repositories/agent-harness/session-backup/mongo';
import type { SessionBackup } from '../../../libs/repositories/agent-harness/session-backup';
import { initDocStore } from '../../../libs/repositories/doc-store';
import { docStore } from '../../../libs/repositories/store-select';
import { SessionBackupRefused } from '../domain/session-backup';
import { forOrg } from '../../../libs/context/orgContext';

type Options = Record<string, string>;

const parse = (
  argv: readonly string[],
): { command: string; options: Options } => {
  const [command = '', ...rest] = argv;
  const options: Options = {};
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i] ?? '';
    if (!key.startsWith('--') || rest[i + 1] === undefined)
      throw new Error(`bad argument ${key}`);
    options[key.slice(2)] = rest[i + 1] as string;
  }
  return { command, options };
};

const required = (options: Options, ...names: string[]): void => {
  const missing = names.filter((name) => !options[name]);
  if (missing.length > 0)
    throw new Error(`missing ${missing.map((m) => `--${m}`).join(', ')}`);
};

export async function runSessionBackupCli(
  argv: readonly string[],
): Promise<number> {
  let parsed: { command: string; options: Options };
  try {
    parsed = parse(argv);
    if (docStore() !== 'postgres') required(parsed.options, 'mongo-uri', 'db');
    if (parsed.command === 'export')
      required(parsed.options, 'org', 'session', 'out');
    else if (parsed.command === 'restore') required(parsed.options, 'in');
    else throw new Error('the command is export or restore');
  } catch (error) {
    process.stderr.write(
      `${error instanceof Error ? error.message : String(error)}\n`,
    );
    return 2;
  }
  const { command, options } = parsed;
  const postgres = docStore() === 'postgres';
  if (postgres) await initDocStore('session-backup-cli', { jobs: false });
  const connection = postgres
    ? null
    : await openMongoConnection(
        options['mongo-uri'] as string,
        options['db'] as string,
      );
  try {
    const backup: SessionBackup =
      connection === null
        ? new PgSessionBackup()
        : new MongoSessionBackup(connection.db);
    const manifest =
      command === 'export'
        ? // The operator names the org the export is for, as a job names the org it works on.
          await forOrg(options['org'] as string, 'session-backup', () =>
            backup.export(
              options['org'] as string,
              options['session'] as string,
              options['out'] as string,
            ),
          )
        : await backup.restore(options['in'] as string);
    const counts = Object.fromEntries(
      Object.entries(manifest.collections).map(([name, entry]) => [
        name,
        entry.documents,
      ]),
    );
    process.stdout.write(
      `${JSON.stringify({ command, sessionId: manifest.sessionId, digest: manifest.digest, counts })}\n`,
    );
    return 0;
  } catch (error) {
    if (error instanceof SessionBackupRefused) {
      process.stdout.write(
        `${JSON.stringify({ command, refused: error.reasonCode, message: error.message })}\n`,
      );
      return 1;
    }
    throw error;
  } finally {
    await connection?.close();
  }
}

if (require.main === module) {
  runSessionBackupCli(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (error: unknown) => {
      process.stderr.write(
        `${error instanceof Error ? (error.stack ?? error.message) : String(error)}\n`,
      );
      process.exit(2);
    },
  );
}
