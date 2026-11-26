/**
 * `finalize --yes` (R46-CARRY, R45CARRY-FU-5): ends the bridge window of a move to PostgreSQL. The key map (both forms) exists only so
 * the follower can remove the row a deleted document became and a rollback can give every carried row its document's id back; once
 * the operator retires the reverse follower and gives up the rollback, it is removed, and an online rollback is refused from then on
 * (it would give every carried row a new id). It refuses unless PostgreSQL is the store, MongoDB is retired, the last milestone of
 * the move is the flip to PostgreSQL, and no job runs (the reverse follower attaches to one).
 */
import type { Log } from './bulk';
import { readMongoFence } from './fence';
import { BUSY, notPostgresStore, whileExclusive } from './reverse';
import type { MigrationRuntime } from './runtime';
import { JOB_KIND, JobState } from './state';
import { clearKeys } from './carry/keymap';
import { clearRefusals } from './refusals';

/** The phases that end a stage of a move, in the job records: the flip (offline, online), a rollback, and the end of the bridge. */
const MILESTONES = ['done', 'cutover_done', 'rolled_back', 'finalised'];

/** The phase of the most recent milestone, or null before the first. */
export async function lastMilestone(
  rt: MigrationRuntime,
): Promise<string | null> {
  const { rows } = await rt.migratePool.query<{ phase: string }>(
    `/* pipeshub:migrate.last_milestone */ SELECT progress->>'phase' AS phase FROM platform.jobs WHERE kind = $1 AND progress->>'phase' = ANY($2::text[])
      ORDER BY id DESC LIMIT 1`,
    [JOB_KIND, MILESTONES],
  );
  return rows[0]?.phase ?? null;
}

export const FINALISED =
  'the move was finalised: the key map is gone, so an online rollback would give every carried row a new id; run `rollback --reverse` (a full copy back) if MongoDB must become the store again';

export interface FinalizeReport {
  ok: boolean;
  message: string;
  /** Key map entries removed (both forms). */
  keys?: number;
}

export function finalize(
  rt: MigrationRuntime,
  o: { log: Log },
): Promise<FinalizeReport> {
  return whileExclusive<FinalizeReport>(
    rt,
    () => ({ ok: false, message: BUSY }),
    () => finalizeLocked(rt, o),
  );
}

async function finalizeLocked(
  rt: MigrationRuntime,
  o: { log: Log },
): Promise<FinalizeReport> {
  const notStore = await notPostgresStore(rt);
  if (notStore)
    return {
      ok: false,
      message: notStore.replace(
        'there is nothing to roll back',
        'there is no move to finalise',
      ),
    };
  if ((await readMongoFence(rt)).mode !== 'retired')
    return {
      ok: false,
      message: 'MongoDB is not retired: the move to PostgreSQL is not complete',
    };
  if (await JobState.current(rt))
    return {
      ok: false,
      message:
        'a migration job is running (the reverse follower, or a step of the move): stop it first, then finalise',
    };
  const last = await lastMilestone(rt);
  if (last === 'finalised')
    return { ok: true, message: 'the move was already finalised', keys: 0 };
  if (last !== 'done' && last !== 'cutover_done')
    return {
      ok: false,
      message: `the last step of the move was '${String(last)}', not the flip to PostgreSQL: nothing to finalise`,
    };
  const { js } = await JobState.attachOrStart(rt);
  try {
    const keys = await rt.jobs(
      async (_t, k) => {
        const { rows } = await k.query<{ n: string }>(
          `/* pipeshub:migrate.keymap_count */ SELECT ((SELECT count(*) FROM platform.docstore_keymap) + (SELECT count(*) FROM platform.docstore_keymap_deployment))::text AS n`,
        );
        await clearKeys(k);
        await clearRefusals(k);
        return Number(rows[0]?.n ?? 0);
      },
      { write: true },
    );
    js.progress.phase = 'finalised';
    await js.checkpoint();
    await js.finish('done');
    o.log('finalised', { keys });
    return {
      ok: true,
      message:
        'the move is final: the key map was removed and an online rollback is no longer offered',
      keys,
    };
  } catch (e) {
    await js.finish('failed').catch(() => undefined);
    throw e;
  }
}
