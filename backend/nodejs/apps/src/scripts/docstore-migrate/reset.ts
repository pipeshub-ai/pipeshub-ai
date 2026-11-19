/**
 * `reset --yes`: empties the tables the copy fills and cancels the running job, so that the copy can start again from nothing
 * (after a source problem was fixed). It refuses while MongoDB is marked retired (PostgreSQL is then the live store).
 */
import { MAPPINGS } from './mapping';
import { readMongoFence } from './fence';
import { clearMarkers } from './follow';
import type { MigrationRuntime } from './runtime';
import { COPY_LOCK_KEY, JobState } from './state';

const IDENT = /^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$/;

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
    for (const t of unique)
      await k.query(`/* pipeshub:reset.delete */ DELETE FROM ${t}`);
  });
  await clearMarkers(rt);
  return {
    ok: true,
    message:
      'the copied rows were removed and the job was cancelled; run the copy again',
    tables: unique.length,
  };
}
