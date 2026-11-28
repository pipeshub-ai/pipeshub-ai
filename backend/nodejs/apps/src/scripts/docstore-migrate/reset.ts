/**
 * `reset --yes`: empties the tables the copy fills and cancels the running job, so that the copy can start again from nothing
 * (after a source problem was fixed). It refuses while MongoDB is marked retired (PostgreSQL is then the live store).
 */
import { MAPPINGS } from './mapping';
import { readMongoFence } from './fence';
import { clearMarkers } from './follow';
import type { MigrationRuntime } from './runtime';
import { COPY_LOCK_KEY, JobState, releaseHeld } from './state';
import { BUSY, whileExclusive } from './reverse';
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

type ResetReport = { ok: boolean; message: string; tables: number };

/** Under the rollback's lock: a reset never runs while a rollback (or its release) does. */
export async function reset(
  rt: MigrationRuntime,
  o: { force: boolean; abandonRelease?: boolean },
): Promise<ResetReport> {
  return whileExclusive<ResetReport>(
    rt,
    () => ({ ok: false, message: BUSY, tables: 0 }),
    () => resetLocked(rt, o),
  );
}

async function resetLocked(
  rt: MigrationRuntime,
  o: { force: boolean; abandonRelease?: boolean },
): Promise<ResetReport> {
  // a rollback's release lives only in its job: cancelling the job would lose what is still to let go
  const held = releaseHeld((await JobState.current(rt))?.progress);
  if (held && !o.abandonRelease)
    return {
      ok: false,
      message: `a rollback has not finished letting go of what the move held in PostgreSQL (${held}): run \`rollback\` again first, or \`reset --yes --abandon-release\` to give that release up (it is recorded in the cancelled job)`,
      tables: 0,
    };
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
  let abandoned = '';
  if (cur && held) {
    const js = await JobState.attach(rt, { releasing: true });
    js.progress.releaseAbandoned = {
      at: new Date().toISOString(),
      pending: js.progress.release?.pending ?? [],
    };
    await js.checkpoint();
    abandoned = ` The release a rollback left (${held}) was given up and recorded in job ${String(js.id)}.`;
  }
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
    message: `the copied rows were removed and the job was cancelled; run the copy again.${abandoned}`,
    tables: unique.length,
  };
}
