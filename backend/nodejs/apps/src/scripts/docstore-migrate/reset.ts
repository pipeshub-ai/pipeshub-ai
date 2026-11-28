/**
 * `reset --yes`: empties the tables the copy fills and cancels the running job, so that the copy can start again from nothing
 * (after a source problem was fixed). It refuses while MongoDB is marked retired (PostgreSQL is then the live store).
 */
import { MAPPINGS } from './mapping';
import { readMongoFence } from './fence';
import { clearMarkers } from './follow';
import type { MigrationRuntime } from './runtime';
import { COPY_LOCK_KEY, JobState } from './state';
import { clearCarried } from './carry/run';
import { clearRefusals } from './refusals';

const IDENT = /^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$/;

const sideTables = (): string[] =>
  [...new Set(MAPPINGS.flatMap((m) => m.sideTables ?? []))].filter((t) =>
    IDENT.test(t),
  );

/**
 * Whether a reset stopped between its two steps: no organisation copied, yet a side table still holds rows of the copy before
 * it. A copy then refuses to start, since those rows would stand for organisations it does not copy.
 */
export async function unfinishedReset(rt: MigrationRuntime): Promise<boolean> {
  for (const t of sideTables()) {
    const { rows } = await rt.migratePool.query<{ n: boolean }>(
      `/* pipeshub:reset.side_rows */ SELECT EXISTS (SELECT 1 FROM ${t}) AS n`,
    );
    if (rows[0]?.n !== true) continue;
    const orgs = await rt.jobs(
      async (_tx, k) =>
        (
          await k.query<{ n: boolean }>(
            '/* pipeshub:reset.orgs_left */ SELECT EXISTS (SELECT 1 FROM identity.orgs) AS n',
          )
        ).rows[0]?.n === true,
      { write: false },
    );
    if (!orgs) return true;
  }
  return false;
}

export async function reset(
  rt: MigrationRuntime,
  o: { force: boolean },
): Promise<{ ok: boolean; message: string; tables: number }> {
  if (!o.force && (await readMongoFence(rt)).mode === 'retired')
    return {
      ok: false,
      message:
        'MongoDB is marked retired, so PostgreSQL is the live store: reset would delete live data (use rollback, or --force if you are sure)',
      tables: 0,
    };
  const tables: string[] = [];
  for (const m of [...MAPPINGS].reverse()) {
    for (const c of m.children ?? []) tables.push(c.table);
    tables.push(m.table);
  }
  const unique = [...new Set(tables)].filter((t) => IDENT.test(t));
  // cancel first: a copy that is still running stops before its next batch, and the exclusive lock below waits for the batches
  // already being written, so nothing is written after the rows are removed
  const cur = await JobState.current(rt);
  if (cur)
    await rt.migratePool.query(
      "SELECT platform.job_finish($1::bigint, 'cancelled')",
      [cur.id],
    );
  await rt.jobs(async (_tx, k) => {
    await k.query(
      '/* pipeshub:migrate.copy_lock */ SELECT pg_advisory_xact_lock($1)',
      [COPY_LOCK_KEY],
    );
    // the carriers' rows first (a carried field hangs on a user, an org or a chat), and the whole key map
    await clearCarried(k);
    await clearRefusals(k);
    for (const t of unique)
      await k.query(`/* pipeshub:reset.delete */ DELETE FROM ${t}`);
  });
  // A separate statement with the migrate login: the import role holds no DELETE on a global table (rule 26), and no foreign key
  // can tie these rows to their organisation (rule 8b: a key from a global table into an org table is an existence oracle).
  // Safe to run again: a reset that stopped here is finished by the next one, and a copy refuses to start until it is
  // (`unfinishedReset`).
  for (const t of sideTables())
    await rt.migratePool.query(
      `/* pipeshub:reset.delete_side */ DELETE FROM ${t}`,
    );
  await clearMarkers(rt);
  return {
    ok: true,
    message:
      'the copied rows were removed and the job was cancelled; run the copy again',
    tables: unique.length,
  };
}
