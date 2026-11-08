/**
 * `migrate --offline` (migration design section 9, compose): the application is stopped, MongoDB and PostgreSQL are up. One run:
 * preflight, schema, bulk copy, post-load checks, verify; the flip is not done here (the operator sets `DOC_STORE=postgres`),
 * but the old store is fenced against stale writers and the new one is marked live.
 */
import { Log, bulk } from './bulk';
import { clearMarkers } from './follow';
import {
  appClients,
  describeClients,
  readMongoFence,
  setMongoFence,
} from './fence';
import {
  ACTIVE_FENCE_TRUST_MS,
  WRITE_TIMEOUT_MS,
} from '../../libs/repositories/fence';
import { PreflightReport, preflight } from './preflight';
import type { MigrationRuntime } from './runtime';
import { postLoad, runMigrateJob } from './schema';
import { JobState } from './state';
import { VerifyReport, verifyAll } from './verify';

/** How long to wait after `readonly` for an application that is still connected (`--allow-clients`). */
const writerGraceMs = (rt: MigrationRuntime): number =>
  rt.cfg.writerGraceMs ?? ACTIVE_FENCE_TRUST_MS + WRITE_TIMEOUT_MS + 500;

export interface OfflineOptions {
  dryRun: boolean;
  allowWriters: boolean;
  log: Log;
}

export interface OfflineReport {
  ok: boolean;
  stage: string;
  preflight?: PreflightReport;
  postLoad?: { ok: boolean; problems: string[] };
  verify?: VerifyReport;
  message: string;
}

export async function migrateOffline(
  rt: MigrationRuntime,
  o: OfflineOptions,
): Promise<OfflineReport> {
  const say = (m: string): void => {
    o.log('step', { m });
  };
  if (!o.allowWriters) {
    const clients = await appClients(rt);
    if (clients.length > 0)
      return {
        ok: false,
        stage: 'quiesce',
        message: `the application is still connected: ${describeClients(clients)}. Stop it first (docker compose stop <app services>; scripts/migrate-to-postgres.sh does this), or pass --allow-clients if those connections are not the application's`,
      };
  }
  const fenceBefore = await readMongoFence(rt);
  if (fenceBefore.mode === 'retired') {
    return {
      ok: false,
      stage: 'fence',
      message:
        'MongoDB is already marked retired: the move was done (or rolled back is needed): run `rollback` first',
    };
  }
  say('schema');
  await runMigrateJob(rt, (m) => {
    say(m);
  });
  say('preflight');
  const pre = await preflight(rt, {
    log: (m) => {
      say(m);
    },
    allowClients: o.allowWriters,
  });
  if (!pre.ok)
    return {
      ok: false,
      stage: 'preflight',
      preflight: pre,
      message: 'preflight found problems; nothing was copied',
    };
  if (o.dryRun)
    return {
      ok: true,
      stage: 'dry_run',
      preflight: pre,
      message: 'dry run: preflight is clean; nothing was copied',
    };

  const js = await JobState.attach(rt);
  js.progress.mode = 'offline';
  js.progress.preflight = { at: pre.at, ok: true, red: 0 };
  await js.checkpoint();
  // draining first, always: the fence module waits out the writes that passed the fence earlier (see fence.ts) before it allows readonly
  await setMongoFence(rt, 'draining');
  await setMongoFence(rt, 'readonly');
  if (o.allowWriters) {
    // a still-connected application trusts `active` for up to ACTIVE_FENCE_TRUST_MS and a write it has begun is cut at WRITE_TIMEOUT_MS
    say('waiting for writes that began before the fence to end');
    await new Promise((r) => setTimeout(r, writerGraceMs(rt)));
  }
  say('bulk');
  await bulk(rt, js, { log: o.log });
  say('post_load');
  const pl = await postLoad(rt, (m) => {
    say(m);
  });
  js.progress.postLoad = { appliedAt: new Date().toISOString() };
  if (!pl.ok) {
    await js.checkpoint();
    return {
      ok: false,
      stage: 'post_load',
      preflight: pre,
      postLoad: pl,
      message:
        'post-load checks failed; MongoDB is unchanged and still the store',
    };
  }
  say('verify');
  const v = await verifyAll(rt, { log: o.log });
  js.progress.phase = 'verified';
  js.progress.verify = { at: v.at, ok: v.ok, diffs: v.diffs };
  await js.checkpoint();
  if (!v.ok) {
    // a verify diff aborts the flip: the old store is the live one again, nothing was lost
    await setMongoFence(
      rt,
      fenceBefore.mode === 'draining' ? 'active' : fenceBefore.mode,
    );
    return {
      ok: false,
      stage: 'verify',
      preflight: pre,
      postLoad: pl,
      verify: v,
      message:
        'verify found differences; the flip is aborted and MongoDB stays the store',
    };
  }
  // PostgreSQL becomes the store before MongoDB is retired, and only if exactly one row says so: a failure here leaves MongoDB read-only
  // and the job running, so the move can be repeated or rolled back, instead of ending with no store that is live
  const flip = await rt.migratePool.query(
    "UPDATE platform.store_epoch SET backend = 'postgres', readonly = false, updated_at = now()",
  );
  if (flip.rowCount !== 1)
    throw new Error(
      `platform.store_epoch has ${String(flip.rowCount)} rows, not one: PostgreSQL was not made the store and MongoDB is not retired (it stays read-only; run rollback to reopen it)`,
    );
  await setMongoFence(rt, 'retired', fenceBefore.epoch + 1);
  js.progress.phase = 'done';
  js.progress.flip = { at: new Date().toISOString(), backend: 'postgres' };
  await js.checkpoint();
  await js.finish('done');
  await clearMarkers(rt);
  return {
    ok: true,
    stage: 'done',
    preflight: pre,
    postLoad: pl,
    verify: v,
    message:
      'copied and verified; set DOC_STORE=postgres and start the application',
  };
}
