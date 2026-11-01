/**
 * `drain` (migration design section 9 step 1, F1 to F3 for a compose install): asks the running application to take no new
 * chat turns, waits (bounded) for the turns in flight to finish, and reports. It is run inside the running stack; the next
 * step is `docker compose stop`, whose graceful stop ends the consumers and the outbox dispatcher (what is unsent is copied
 * with its state, section 7).
 */
import type { MigrationRuntime } from './runtime';
import { casMongoFence, readMongoFence } from './fence';
import type { Log } from './bulk';

/** A turn counts as running while its session says so and its activity is this fresh (the application's own window). */
export const ACTIVE_RUN_WINDOW_MS = 10 * 60 * 1000;

export interface DrainReport {
  ok: boolean;
  stoppedRuns: number;
  waitedMs: number;
  remaining: number;
  forced: boolean;
  fence: string;
}

async function running(rt: MigrationRuntime): Promise<Array<{ _id: unknown }>> {
  return rt
    .db()
    .collection('chatSessions')
    .find(
      {
        status: 'Inprogress',
        lastActivityAt: { $gt: Date.now() - ACTIVE_RUN_WINDOW_MS },
      },
      { projection: { _id: 1 } },
    )
    .limit(1000)
    .toArray();
}

export async function drain(
  rt: MigrationRuntime,
  o: {
    waitMs: number;
    force: boolean;
    log: Log;
    pollMs?: number;
    settleMs?: number;
  },
): Promise<DrainReport> {
  const t0 = Date.now();
  const before = (await readMongoFence(rt)).mode;
  if (before === 'readonly' || before === 'retired') {
    // a store that is being or has been moved is not drained: this would un-retire it
    return {
      ok: false,
      stoppedRuns: 0,
      waitedMs: 0,
      remaining: 0,
      forced: false,
      fence: before,
    };
  }
  if (!(await casMongoFence(rt, ['active', 'draining'], 'draining')))
    return {
      ok: false,
      stoppedRuns: 0,
      waitedMs: 0,
      remaining: 0,
      forced: false,
      fence: (await readMongoFence(rt)).mode,
    };
  o.log('drain_requested', { waitMs: o.waitMs });
  // an application reads the fence afresh when it admits a turn; a short pause covers a turn that was admitted just before the fence moved
  await new Promise((r) => setTimeout(r, o.settleMs ?? 1000));
  let left = await running(rt);
  while (left.length > 0 && Date.now() - t0 < o.waitMs) {
    await new Promise((r) => setTimeout(r, o.pollMs ?? 2000));
    left = await running(rt);
  }
  let stopped = 0;
  let forced = false;
  if (left.length > 0 && o.force) {
    // the answer of a run that never finished was not saved; the session is closed as interrupted so that it is not left "in progress"
    const r = await rt
      .db()
      .collection('chatSessions')
      .updateMany({ _id: { $in: left.map((d) => d._id) } } as never, {
        $set: { status: 'Stopped', failReason: 'interrupted' },
        $unset: { activeRunId: '' },
      });
    stopped = r.modifiedCount;
    forced = true;
    left = await running(rt);
  }
  const fence = (await readMongoFence(rt)).mode;
  return {
    ok: left.length === 0,
    stoppedRuns: stopped,
    waitedMs: Date.now() - t0,
    remaining: left.length,
    forced,
    fence,
  };
}

/** Ends a drain: `draining` becomes `active`; a fence in any other mode is left alone. Says whether it changed anything. */
export async function cancelDrain(rt: MigrationRuntime): Promise<boolean> {
  return casMongoFence(rt, ['draining'], 'active');
}
