/**
 * The online path (migration design sections 4, 6, 10; Helm installs): the application keeps running while the data is copied.
 *
 *  `start`    preflight (replica set, oplog), a change-stream token, the bulk copy, the post-load step, catch-up, and a verify that
 *             converges while the application writes. It ends in steady state (`online_ready`): `follow run` keeps the copy level.
 *  `verify`   incremental: only the documents that changed since the last verify pass (read back from the change stream), and
 *             optionally a hash-chosen share of all documents. Moves the verify token on.
 *  `cutover`  F0..F9: pre-checks, drain, freeze (the Mongo fence goes read-only), final catch-up up to a marker, incremental verify,
 *             flip (PostgreSQL becomes the store at a new epoch, Mongo is retired), and a budget for the freeze. Any failed check
 *             unfreezes and leaves MongoDB the store: nothing was written to PostgreSQL by the application.
 *  `unfreeze` the same abort, by hand.
 */
import { randomUUID } from 'node:crypto';
import { Timestamp } from 'mongodb';
import { Log, bulk } from './bulk';
import { drain } from './drain';
import type { DocDiff } from './compare';
import { casMongoFence, readMongoFence, setMongoFence } from './fence';
import {
  MARKER_COLLECTION,
  follow,
  followStart,
  majorityPoint,
} from './follow';
import { MAPPINGS } from './mapping';
import { PreflightReport, preflight } from './preflight';
import type { MigrationRuntime } from './runtime';
import { postLoad } from './schema';
import { JobState } from './state';
import { VerifyReport, verifyAll } from './verify';
import { ACTIVE_FENCE_TRUST_MS } from '../../libs/repositories/fence';

const BY_COLLECTION = new Set(MAPPINGS.map((m) => m.collection));

/** Writes a marker document into the application database and returns its id: once the stream delivers it, everything before it has been seen. */
async function writeMarker(rt: MigrationRuntime): Promise<string> {
  const id = randomUUID();
  await rt
    .db()
    .collection<{ _id: string }>(MARKER_COLLECTION)
    .insertOne({ _id: id, at: new Date() } as never);
  return id;
}

/** Applies the stream up to a new marker. False when the marker did not come within `timeoutMs`. */
export async function catchUp(
  rt: MigrationRuntime,
  js: JobState,
  log: Log,
  timeoutMs: number,
  extra: {
    watchAfter?: Timestamp;
    stragglerSeconds?: number;
  } = {},
): Promise<{
  ok: boolean;
  lagMs: number;
  appEventsAfter: number;
  stragglers: number;
}> {
  const marker = await writeMarker(rt);
  const r = await follow(rt, js, {
    untilIdleMs: 0,
    untilMarker: marker,
    stop: AbortSignal.timeout(timeoutMs),
    log,
    ...extra,
  });
  await rt
    .db()
    .collection(MARKER_COLLECTION)
    .deleteMany({ at: { $lt: new Date(Date.now() - 60_000) } })
    .catch(() => undefined);
  return {
    ok: r.markerSeen,
    lagMs: r.lagMs,
    appEventsAfter: r.appEventsAfter,
    stragglers: r.stragglers,
  };
}

/** The ids (per mapped collection) that changed after `token`, up to a new marker: the worklist of an incremental verify. */
export async function changedIds(
  rt: MigrationRuntime,
  token: unknown,
  timeoutMs: number,
): Promise<Record<string, string[]> | null> {
  const marker = await writeMarker(rt);
  const dbs = [rt.cfg.mongoDb, ...(rt.cfg.slackDb ? [rt.cfg.slackDb] : [])];
  const stream = rt.mongo.watch([{ $match: { 'ns.db': { $in: dbs } } }], {
    resumeAfter: token,
    promoteLongs: false,
  });
  const out = new Map<string, Set<string>>();
  const until = Date.now() + timeoutMs;
  try {
    while (Date.now() < until) {
      const e = (await stream.tryNext()) as {
        ns?: { coll: string };
        documentKey?: { _id: unknown };
      } | null;
      if (!e) {
        await new Promise((r) => setTimeout(r, 100));
        continue;
      }
      const coll = e.ns?.coll ?? '';
      if (coll === MARKER_COLLECTION) {
        if (String(e.documentKey?._id) === marker)
          return Object.fromEntries([...out].map(([c, ids]) => [c, [...ids]]));
        continue;
      }
      if (!BY_COLLECTION.has(coll) || !e.documentKey) continue;
      const set = out.get(coll) ?? new Set<string>();
      set.add(String(e.documentKey._id).toLowerCase());
      out.set(coll, set);
    }
    return null;
  } finally {
    await stream.close().catch(() => undefined);
  }
}

/**
 * Runs a verify while the application writes. Every pass starts from a marker the follower has applied, so a difference is one of two
 * things: a document that changed in MongoDB after that point (it is "hot": the follower has not got there yet, and it is looked at
 * again in the next round), or a real difference (nothing is on its way that would change it). Only real differences fail the verify;
 * the freeze looks at the hot ones again with the writes stopped (F7).
 */
export async function verifyConverge(
  rt: MigrationRuntime,
  js: JobState,
  log: Log,
  scope: { sample?: number; ids?: Record<string, string[]> } = {},
  rounds = 6,
): Promise<{
  ok: boolean;
  remaining: number;
  hot: number;
  rounds: number;
  report: VerifyReport;
  /** The differences that were not on their way (at most 20), so a failure names the documents. */
  left: Array<DocDiff & { collection: string }>;
}> {
  let pending: Record<string, string[]> | undefined = scope.ids;
  let first: unknown;
  let remaining = 0;
  let hot = 0;
  let round = 0;
  let report: VerifyReport | undefined;
  let left: Array<DocDiff & { collection: string }> = [];
  while (round < rounds) {
    round += 1;
    await catchUp(rt, js, log, 120_000);
    const token = js.progress.follow?.resumeToken;
    first ??= token;
    const diffs = new Map<string, DocDiff[]>();
    report = await verifyAll(rt, {
      log,
      ...(round === 1 && scope.sample !== undefined
        ? { sample: scope.sample }
        : {}),
      ...(pending ? { ids: pending } : {}),
      onDiff: (c, d) => {
        diffs.set(c, [...(diffs.get(c) ?? []), d]);
      },
    });
    if (diffs.size === 0) {
      remaining = 0;
      hot = 0;
      break;
    }
    const later =
      (token !== undefined && (await changedIds(rt, token, 120_000))) || {};
    const next: Record<string, string[]> = {};
    remaining = 0;
    hot = 0;
    left = [];
    for (const [c, list] of diffs) {
      const changed = new Set(later[c] ?? []);
      for (const d of list) {
        if (changed.has(d.id.toLowerCase())) hot += 1;
        else {
          remaining += 1;
          if (left.length < 20) left.push({ collection: c, ...d });
        }
        (next[c] ??= []).push(d.id);
      }
    }
    pending = next;
    if (remaining === 0 && hot === 0) break;
  }
  const ok = remaining === 0;
  if (ok && first !== undefined)
    js.progress.verify = {
      at: new Date().toISOString(),
      ok: true,
      diffs: 0,
      token: first,
    };
  await js.checkpoint();
  return {
    ok,
    remaining,
    hot,
    rounds: round,
    report: report as VerifyReport,
    left: ok ? [] : left,
  };
}

export interface OnlineReport {
  ok: boolean;
  stage: string;
  message: string;
  preflight?: PreflightReport;
  verify?: {
    ok: boolean;
    remaining: number;
    hot?: number;
    rounds: number;
    left?: Array<DocDiff & { collection: string }>;
  };
  lagMs?: number;
}

export async function onlineStart(
  rt: MigrationRuntime,
  o: { dryRun: boolean; sample?: number; log: Log },
): Promise<OnlineReport> {
  const fence = await readMongoFence(rt);
  if (fence.mode !== 'active')
    return {
      ok: false,
      stage: 'fence',
      message: `the MongoDB fence is '${fence.mode}', not active: nothing to copy online`,
    };
  const pre = await preflight(rt, {
    allowClients: true,
    online: true,
    log: (m) => {
      o.log('preflight', { m });
    },
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
  js.progress.mode = 'online';
  js.progress.preflight = { at: pre.at, ok: true, red: 0 };
  // PostgreSQL is not the store yet: a process that starts on it must be refused until the flip
  await rt.migratePool
    .query(
      "UPDATE platform.store_epoch SET backend = 'mongo', readonly = true, updated_at = now() WHERE NOT (backend = 'mongo' AND readonly)",
    )
    .catch(() => undefined);
  await followStart(rt, js);
  o.log('step', { m: 'bulk copy (the application keeps writing)' });
  await bulk(rt, js, { log: o.log });
  o.log('step', { m: 'post_load' });
  const pl = await postLoad(rt, (m) => {
    o.log('step', { m });
  });
  js.progress.postLoad = { appliedAt: new Date().toISOString() };
  await js.checkpoint();
  if (!pl.ok)
    return {
      ok: false,
      stage: 'post_load',
      preflight: pre,
      message: `post-load checks failed: ${pl.problems.join('; ')}`,
    };
  o.log('step', { m: 'catching up with the change stream' });
  const c = await catchUp(rt, js, o.log, 3_600_000);
  if (!c.ok)
    return {
      ok: false,
      stage: 'catch_up',
      preflight: pre,
      message: 'the copy did not catch up with MongoDB within an hour',
    };
  o.log('step', { m: 'verify (converging while the application writes)' });
  const v = await verifyConverge(
    rt,
    js,
    o.log,
    o.sample !== undefined ? { sample: o.sample } : {},
  );
  js.progress.phase = v.ok ? 'online_ready' : 'online_verify_failed';
  await js.checkpoint();
  return {
    ok: v.ok,
    stage: js.progress.phase,
    preflight: pre,
    verify: {
      ok: v.ok,
      remaining: v.remaining,
      hot: v.hot,
      rounds: v.rounds,
      ...(v.left.length ? { left: v.left } : {}),
    },
    lagMs: c.lagMs,
    message: v.ok
      ? 'copied and level with MongoDB; keep `follow run` going and run `cutover` when ready'
      : `${String(v.remaining)} documents still differ after ${String(v.rounds)} rounds: read the verify report, repair, run verify again`,
  };
}

/** One incremental verify pass: only what changed since the last one (plus a share of all documents when `sample` is set). */
export async function onlineVerify(
  rt: MigrationRuntime,
  o: { sample?: number; log: Log },
): Promise<OnlineReport & { changed?: number }> {
  const js = await JobState.attach(rt);
  const token = js.progress.verify?.token;
  if (token === undefined || js.progress.verify?.ok !== true)
    return {
      ok: false,
      stage: 'verify',
      message:
        'no verified state to continue from: `migrate --online` ends with one',
    };
  const c = await catchUp(rt, js, o.log, 120_000);
  if (!c.ok)
    return {
      ok: false,
      stage: 'catch_up',
      message: 'the copy did not catch up within two minutes',
    };
  const ids = await changedIds(rt, token, 300_000);
  if (!ids)
    return {
      ok: false,
      stage: 'changed_ids',
      message:
        'the changes since the last verify could not be read (the token may have fallen out of the oplog): run a full verify',
    };
  const changed = Object.values(ids).reduce((a, l) => a + l.length, 0);
  const v = await verifyConverge(rt, js, o.log, { ids });
  const sampled =
    v.ok && o.sample !== undefined
      ? await verifyConverge(rt, js, o.log, { sample: o.sample })
      : undefined;
  const ok = v.ok && (sampled?.ok ?? true);
  return {
    ok,
    stage: 'verify',
    changed,
    verify: {
      ok,
      remaining: v.remaining + (sampled?.remaining ?? 0),
      rounds: v.rounds,
    },
    message: ok
      ? `${String(changed)} changed documents verified`
      : `${String(v.remaining + (sampled?.remaining ?? 0))} documents differ`,
  };
}

export interface PrecheckItem {
  name: string;
  ok: boolean;
  detail?: unknown;
}

/** F0: everything that must hold before the first step. */
export async function precheck(
  rt: MigrationRuntime,
  js: JobState,
  o: {
    log: Log;
    lagMaxMs: number;
    fenceArmed: boolean;
    shadowClean: boolean;
  },
): Promise<{ ok: boolean; items: PrecheckItem[] }> {
  const items: PrecheckItem[] = [];
  const add = (name: string, ok: boolean, detail?: unknown): void => {
    items.push({ name, ok, ...(detail !== undefined ? { detail } : {}) });
  };
  add(
    'verified state',
    js.progress.mode === 'online' &&
      js.progress.verify?.ok === true &&
      js.progress.verify.token !== undefined,
    { phase: js.progress.phase },
  );
  add('post_load applied', js.progress.postLoad !== undefined);
  const fence = await readMongoFence(rt);
  add('Mongo fence is active', fence.mode === 'active', fence.mode);
  const c = await catchUp(rt, js, o.log, 60_000).catch(() => ({
    ok: false,
    lagMs: -1,
    appEventsAfter: 0,
    stragglers: 0,
  }));
  add('follower caught up', c.ok && c.lagMs < o.lagMaxMs, {
    lagMs: c.lagMs,
    limitMs: o.lagMaxMs,
  });
  try {
    const s = await rt.mongo.db('admin').command({ replSetGetStatus: 1 });
    const healthy = (
      s.members as Array<{ health: number; state: number }>
    ).filter((m) => m.health === 1 && (m.state === 1 || m.state === 2)).length;
    const need = Number(s.writeMajorityCount ?? 1);
    add('majority reachable', healthy >= need, {
      healthyDataVoters: healthy,
      writeMajorityCount: need,
    });
    const point = await majorityPoint(rt);
    add('majority commit point readable', point !== null);
  } catch (e) {
    add('majority reachable', false, (e as Error).message);
  }
  const t = await rt.migratePool
    .query<{
      n: string;
    }>(
      "SELECT count(*)::text AS n FROM pg_trigger WHERE tgname = 'docstore_change_log' AND NOT tgisinternal",
    )
    .catch(() => undefined);
  add(
    'change-log triggers present',
    Number(t?.rows[0]?.n ?? 0) > 0,
    t?.rows[0]?.n ?? 'not readable',
  );
  const ep = await rt.migratePool
    .query<{
      backend: string;
      readonly: boolean;
    }>('SELECT backend, readonly FROM platform.store_epoch')
    .catch(() => undefined);
  add(
    'PostgreSQL store fence is closed (not yet the store)',
    ep?.rows[0]?.backend === 'mongo' && ep.rows[0].readonly,
    ep?.rows[0],
  );
  add('every writer carries the Mongo fence (attested)', o.fenceArmed);
  add('shadow reads showed no difference (attested)', o.shadowClean);
  return { ok: items.every((i) => i.ok), items };
}

/** Abort: MongoDB is writable again. A store that was already retired is left alone. */
export async function unfreeze(rt: MigrationRuntime): Promise<boolean> {
  return casMongoFence(rt, ['readonly', 'draining'], 'active');
}

export interface CutoverOptions {
  log: Log;
  dryRun: boolean;
  drainWaitMs: number;
  force: boolean;
  /** Budget for the freeze (F4 to F8); past it the cut aborts and unfreezes. */
  maxFreezeMs: number;
  /** Wait after the fence goes read-only for writes that were admitted just before. */
  graceMs: number;
  lagMaxMs: number;
  fenceArmed: boolean;
  shadowClean: boolean;
  /** Test hook: fail in this step (as a check would), to prove the abort path. */
  failAt?: string;
}

export interface CutoverReport {
  ok: boolean;
  stage: string;
  message: string;
  steps: Array<{ step: string; ms: number; detail?: unknown }>;
  freezeMs?: number;
  precheck?: { ok: boolean; items: PrecheckItem[] };
  verify?: { ok: boolean; remaining: number; changed: number };
  aborted?: boolean;
}

export async function cutover(
  rt: MigrationRuntime,
  o: CutoverOptions,
): Promise<CutoverReport> {
  const steps: CutoverReport['steps'] = [];
  const js = await JobState.attach(rt);
  const mark = async <T>(
    step: string,
    fn: () => Promise<T>,
    detail?: (r: T) => unknown,
  ): Promise<T> => {
    const t = Date.now();
    const r = await fn();
    steps.push({
      step,
      ms: Date.now() - t,
      ...(detail ? { detail: detail(r) } : {}),
    });
    return r;
  };
  const before = await readMongoFence(rt);
  if (before.mode === 'retired' || before.mode === 'readonly')
    return {
      ok: false,
      stage: 'fence',
      steps,
      message: `the MongoDB fence is '${before.mode}': the store was moved or a move is running`,
    };

  const pc = await mark('F0 precheck', () =>
    precheck(rt, js, {
      log: o.log,
      lagMaxMs: o.lagMaxMs,
      fenceArmed: o.fenceArmed,
      shadowClean: o.shadowClean,
    }),
  );
  if (!pc.ok)
    return {
      ok: false,
      stage: 'F0',
      steps,
      precheck: pc,
      message: 'a pre-check failed; nothing was changed',
    };
  if (o.dryRun)
    return {
      ok: true,
      stage: 'dry_run',
      steps,
      precheck: pc,
      message: 'dry run: all pre-checks passed; nothing was changed',
    };

  let frozenAt = 0;
  const abort = async (stage: string, why: string): Promise<CutoverReport> => {
    const unfrozen = await unfreeze(rt).catch(() => false);
    js.progress.cutover = {
      aborted: { stage, why, at: new Date().toISOString() },
    };
    await js.checkpoint().catch(() => undefined);
    return {
      ok: false,
      stage,
      aborted: true,
      steps,
      ...(frozenAt ? { freezeMs: Date.now() - frozenAt } : {}),
      precheck: pc,
      message: `${why}. Aborted: MongoDB is the store and writable again (${unfrozen ? 'unfrozen' : 'fence unchanged'}); nothing was written to PostgreSQL by the application`,
    };
  };
  const overBudget = (): boolean =>
    frozenAt > 0 && Date.now() - frozenAt > o.maxFreezeMs;

  try {
    // F1/F2: no new chat turns, wait for the ones running
    const d = await mark(
      'F1-F2 drain',
      () =>
        drain(rt, {
          waitMs: o.drainWaitMs,
          force: o.force,
          log: o.log,
          // the operator's attestation that every writer carries the fence is already a pre-check of the cut (F0)
          confirmArmed: o.fenceArmed,
        }),
      (r) => ({ ok: r.ok, remaining: r.remaining, stopped: r.stoppedRuns }),
    );
    if (!d.ok)
      return await abort(
        'F2',
        `${String(d.remaining)} chat turns are still running after ${String(Math.round(o.drainWaitMs / 1000))} s (use --force-drain to close them as interrupted)`,
      );
    // F3: the migrator cannot pause the application's consumers; the Mongo fence refuses their writes (they re-throw and retry)
    steps.push({
      step: 'F3 background writers',
      ms: 0,
      detail:
        'not paused by the migrator: their Mongo writes are refused by the fence from F4 on and retried',
    });

    // F4: read-only
    if (o.failAt === 'F4') throw new Error('injected failure at F4');
    frozenAt = Date.now();
    await mark('F4 read-only', async () => {
      await setMongoFence(rt, 'readonly');
    });
    const tFence = (await rt.db().command({ ping: 1 }))
      .operationTime as Timestamp;
    await mark(
      'F4 grace',
      () => new Promise<void>((r) => setTimeout(r, o.graceMs)),
    );

    // F5: final catch-up to a marker written after the grace
    if (o.failAt === 'F5') throw new Error('injected failure at F5');
    const cu = await mark(
      'F5 final catch-up',
      () =>
        catchUp(rt, js, o.log, Math.min(180_000, o.maxFreezeMs), {
          watchAfter: tFence,
          stragglerSeconds: Math.ceil((ACTIVE_FENCE_TRUST_MS + 2000) / 1000),
        }),
      (r) => ({ lagMs: r.lagMs, stragglers: r.stragglers }),
    );
    if (!cu.ok)
      return await abort(
        'F5',
        'the final catch-up did not complete in its budget',
      );
    if (overBudget()) return await abort('F5', 'the freeze budget was used up');

    // F6: the outbox rows travel with their state through the same stream (published rows are not copied, publishing becomes pending)
    steps.push({
      step: 'F6 outbox state',
      ms: 0,
      detail: 'continuous (the outbox mapping copies state); checked in F7',
    });

    // F7: incremental verify of what changed since the last pass
    if (o.failAt === 'F7') throw new Error('injected failure at F7');
    const token = js.progress.verify?.token;
    const v = await mark(
      'F7 incremental verify',
      async () => {
        const ids = await changedIds(rt, token, 240_000);
        if (!ids) return null;
        const changed = Object.values(ids).reduce((a, l) => a + l.length, 0);
        const rep = await verifyAll(rt, { log: o.log, ids });
        return { changed, rep };
      },
      (r) => (r ? { changed: r.changed, diffs: r.rep.diffs } : 'unreadable'),
    );
    if (!v)
      return await abort(
        'F7',
        'the changes since the last verify could not be read',
      );
    const failed = v.rep.collections.reduce((a, c) => a + c.diffCount, 0);
    if (failed > 0 || !v.rep.global.every((g) => g.ok))
      return await abort(
        'F7',
        `the final verify found ${String(failed)} differing documents or a failed global check`,
      );
    if (overBudget()) return await abort('F7', 'the freeze budget was used up');

    // test aid: a writer that ignored the fence, after the verify
    if (o.failAt === 'leak-after-F7')
      await rt
        .db()
        .collection('users')
        .updateOne({}, { $set: { fullName: 'leaked past the fence' } });
    // F8: flip. First re-assert that the freeze held: count again now, since F5's count is as old as the F7 verify
    const again = await mark(
      'F8 freeze check',
      () =>
        catchUp(rt, js, o.log, Math.min(60_000, o.maxFreezeMs), {
          watchAfter: tFence,
          stragglerSeconds: Math.ceil((ACTIVE_FENCE_TRUST_MS + 2000) / 1000),
        }),
      (r) => ({ appEventsAfter: r.appEventsAfter, stragglers: r.stragglers }),
    );
    if (!again.ok)
      return await abort(
        'F8',
        'the freeze check did not complete in its budget',
      );
    const leaked = cu.appEventsAfter + again.appEventsAfter + again.stragglers;
    if (leaked > 0)
      return await abort(
        'F8',
        `${String(leaked)} application writes reached MongoDB after the freeze check began (F5 or later): the fence leaked`,
      );
    const pgBefore = (
      await rt.migratePool.query<{ epoch: number }>(
        'SELECT epoch FROM platform.store_epoch',
      )
    ).rows[0];
    const epoch = (pgBefore?.epoch ?? 1) + 1;
    await mark('F8 flip', async () => {
      await rt.migratePool.query(
        "UPDATE platform.store_epoch SET backend = 'postgres', epoch = $1, readonly = false, updated_at = now()",
        [epoch],
      );
      try {
        await setMongoFence(rt, 'retired', before.epoch + 1);
      } catch (e) {
        // MongoDB could not be retired: PostgreSQL goes back to closed, and the abort lets MongoDB write again
        await rt.migratePool.query(
          "UPDATE platform.store_epoch SET backend = 'mongo', epoch = $1, readonly = true, updated_at = now()",
          [epoch - 1],
        );
        throw e;
      }
    });
    const freezeMs = Date.now() - frozenAt;
    js.progress.phase = 'cutover_done';
    js.progress.flip = { at: new Date().toISOString(), backend: 'postgres' };
    js.progress.cutover = { freezeMs, epoch };
    await js.checkpoint();
    return {
      ok: true,
      stage: 'done',
      steps,
      freezeMs,
      precheck: pc,
      verify: { ok: true, remaining: 0, changed: v.changed },
      message: `PostgreSQL is the store at epoch ${String(epoch)} (writes were frozen ${String(Math.round(freezeMs / 1000))} s). Roll the application over to DOC_STORE=postgres now, then start the reverse follower (F9)`,
    };
  } catch (e) {
    return await abort(
      'error',
      `the cut failed: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
}
