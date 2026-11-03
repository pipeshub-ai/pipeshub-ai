#!/usr/bin/env node
/**
 * Moves the application's documents from MongoDB to PostgreSQL (and back). See docs in
 * `libs/repositories/README.md` ("Moving from MongoDB") and `deployment/docker-compose/postgres/README.md`.
 *
 *   node dist/scripts/docstore-migrate.js <command> [flags]
 *     preflight              read-only report; exit 1 when anything is red
 *     schema                 run the migrate job (MIGRATE_SCHEMA_COMMAND)
 *     bulk [--only a,b]      copy every collection (restartable from its checkpoint)
 *     follow start|run       change-stream catch-up (replica sets); `run --until-idle <s>` stops when quiet
 *     post_load              run the migrate job again and check what it left
 *     verify [--only a,b]    compare every document through the independent decoder
 *     drain [--wait s] [--force] [--cancel]   ask the running application for no new chat turns, wait for the turns in flight
 *     migrate --offline [--dry-run] [--allow-writers]   the compose cut, one command
 *     reset --yes            empty the copied tables and cancel the job (start the copy again)
 *     status                 job, phase and per-collection progress
 */
import { runCli } from './docstore-migrate/cli';

runCli(process.argv.slice(2), process.env).then(
  (code) => process.exit(code),
  (e: unknown) => {
    const m = e instanceof Error ? e.message : String(e);
    process.stderr.write(
      `${JSON.stringify({ level: 'error', message: m.replace(/(:\/\/[^:/@\s]+:)[^@\s]*@/g, '$1***@') })}\n`,
    );
    process.exit(2);
  },
);
